import json
import sys
import subprocess
import tempfile
import os
import re
import time
import ast
from http.server import BaseHTTPRequestHandler

MAX_CODE_LENGTH = 15_000
MAX_STDOUT = 10_000
MAX_STDERR = 7_000
MAX_TIMEOUT = 40
DEFAULT_TIMEOUT = 20
MAX_MEMORY_MB = 64
MAX_RECURSION = 200

BLOCKED_MODULES = [
    "os", "subprocess", "shutil", "pathlib", "glob", "tempfile",
    "socket", "urllib", "requests", "httpx", "http", "aiohttp",
    "ftplib", "smtplib", "imaplib", "poplib",
    "multiprocessing", "threading", "signal", "asyncio",
    "ctypes", "cffi", "mmap",
    "pickle", "shelve", "marshal",
    "importlib", "code", "codeop",
    "webbrowser", "antigravity", "turtle",
    "gc", "resource", "atexit", "io", "sys",
    "posix", "nt", "posixpath", "ntpath", "genericpath",
    "_io", "_posixsubprocess", "_signal",
    "pwd", "grp", "fcntl", "termios", "tty", "pty",
    "_frozen_importlib", "_frozen_importlib_external",
    "inspect", "pkgutil", "modulefinder", "ast", "linecache",
]

BLOCKED_BUILTINS = [
    "open", "exec", "eval", "compile", "breakpoint",
    "__import__", "exit", "quit", "help", "input",
    "memoryview", "property", "type", "object",
]

BLOCKED_DUNDERS = [
    "__subclasses__", "__globals__", "__builtins__",
    "__code__", "__bases__", "__mro__",
    "__dict__", "__class__", "__module__",
    "__defaults__", "__kwdefaults__", "__closure__",
    "__self__", "__func__", "__getattribute__",
    "__get__", "__set__", "__delete__",
    "__init_subclass__", "__subclasshook__",
]

BLOCKED_SYSMODULES_KEYS = {
    "posix", "nt", "_io", "_posixsubprocess", "_signal",
    "pwd", "grp", "fcntl", "posixpath", "ntpath", "genericpath",
    "_frozen_importlib", "_frozen_importlib_external",
    "zipimport", "_imp",
}

TMP_FILE_RE = re.compile(r'File "/tmp/[^"]+", ')
LINE_RE = re.compile(r'(?<=line )\d+')

WRAPPER = '''
import sys, builtins

def __sandbox_init():
    try:
        import resource
        _mem = {max_memory} * 1024 * 1024
        for limit in [resource.RLIMIT_AS, resource.RLIMIT_FSIZE, resource.RLIMIT_NPROC]:
            try:
                val = _mem if limit == resource.RLIMIT_AS else 0
                resource.setrlimit(limit, (val, val))
            except Exception as e:
                print(f"Warning: setrlimit {{limit}} failed: {{e}}", file=sys.stderr)
    except Exception:
        pass

    _blocked = set({blocked_set})
    _blocked_sys = set({blocked_sysmodules})
    _blocked_attrs = frozenset({blocked_dunders})

    for k in list(sys.modules.keys()):
        if k in _blocked_sys or k.split(".")[0] in _blocked:
            del sys.modules[k]

    _orig_import = __import__
    def _safe_import(name, *args, **kwargs):
        if name.split(".")[0] in _blocked or name in _blocked_sys:
            raise ImportError(f"Access to '{{name}}' is blocked")
        return _orig_import(name, *args, **kwargs)
    builtins.__import__ = _safe_import

    _orig_getattr = getattr
    def _safe_getattr(obj, name, *args):
        if isinstance(name, str) and (name in _blocked_attrs or (name.startswith("__") and name.endswith("__"))):
             raise AttributeError(f"Access to '{{name}}' is blocked")
        return _orig_getattr(obj, name, *args)
    builtins.getattr = _safe_getattr

    def _safe_setattr(obj, name, value):
        if isinstance(name, str) and (name in _blocked_attrs or (name.startswith("__") and name.endswith("__"))):
            raise AttributeError(f"Setting '{{name}}' is blocked")
        object.__setattr__(obj, name, value)
    builtins.setattr = _safe_setattr

    def _safe_delattr(obj, name):
        if isinstance(name, str) and (name in _blocked_attrs or (name.startswith("__") and name.endswith("__"))):
            raise AttributeError(f"Deleting '{{name}}' is blocked")
        object.__delattr__(obj, name)
    builtins.delattr = _safe_delattr

    for b in {blocked_builtins}:
        setattr(builtins, b, None)
    
    builtins.vars = None
    sys.setrecursionlimit({max_recursion})

__sandbox_init()
del __sandbox_init

{code}
'''

WRAPPER_PREFIX_LINES = WRAPPER.split("{code}")[0].format(
    blocked_set=repr(set(BLOCKED_MODULES)),
    blocked_sysmodules=repr(BLOCKED_SYSMODULES_KEYS),
    blocked_dunders=repr(set(BLOCKED_DUNDERS)),
    blocked_builtins=repr(BLOCKED_BUILTINS),
    max_memory=MAX_MEMORY_MB,
    max_recursion=MAX_RECURSION,
).count("\n")


def check_ast(code):
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]

    blocked = []
    for node in ast.walk(tree):
        # Imports
        if isinstance(node, ast.Import):
            for n in node.names:
                base = n.name.split('.')[0]
                if base in BLOCKED_MODULES:
                    blocked.append(f"module '{base}'")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base = node.module.split('.')[0]
                if base in BLOCKED_MODULES:
                    blocked.append(f"module '{base}'")
            for n in node.names:
                if n.name in BLOCKED_BUILTINS or n.name in BLOCKED_DUNDERS:
                    blocked.append(f"restricted name '{n.name}'")

        # Attribute access (F-01, F-02 bypass)
        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_DUNDERS or (node.attr.startswith("__") and node.attr.endswith("__")):
                 blocked.append(f"attribute access '{node.attr}'")

        # Name access (F-05 bypass via names)
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_BUILTINS or node.id in BLOCKED_DUNDERS:
                blocked.append(f"restricted name '{node.id}'")
            if node.id in ["getattr", "setattr", "delattr", "vars"]:
                blocked.append(f"dynamic attribute helper '{node.id}'")
        
        # String literals (F-05 check) - simplified to prevent overkill but catch obvious ones
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value.lower()
            # Only block strings that EXACTLY match critical blocked items to avoid false positives
            # but allow them if they are common words. 
            # Real defense is in WRAPPER and Name/Attribute checks.
            for b_dunder in BLOCKED_DUNDERS:
                if b_dunder in val:
                    blocked.append(f"suspicious string literal containing '{b_dunder}'")

    return sorted(list(set(blocked)))


def clean_stderr(text):
    text = TMP_FILE_RE.sub("", text)

    def adjust_line(m):
        n = int(m.group(0))
        adjusted = n - WRAPPER_PREFIX_LINES
        return str(max(adjusted, 1))

    text = LINE_RE.sub(adjust_line, text)
    return text


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        print("GET request rejected")
        return self._json(405, {"success": False, "error": "Only POST method is supported"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            print("Empty request body")
            return self._json(400, {"success": False, "error": "Empty body"})

        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as e:
            print(f"JSON parse failed: {e}")
            return self._json(400, {"success": False, "error": "Invalid JSON"})

        code = body.get("code", "")
        stdin_list = body.get("stdin", [])
        timeout = min(max(0.5, body.get("timeout", DEFAULT_TIMEOUT)), MAX_TIMEOUT)

        if not code or not isinstance(code, str):
            print("Missing or invalid code field")
            return self._json(400, {"success": False, "error": "Missing 'code'"})

        if len(code) > MAX_CODE_LENGTH:
            print(f"Code too long: {len(code)} chars")
            return self._json(400, {"success": False, "error": "Code too long"})

        if not isinstance(stdin_list, list):
            print("Invalid stdin type")
            return self._json(400, {"success": False, "error": "'stdin' must be an array"})

        stdin_data = "\n".join(str(item) for item in stdin_list)

        blocked = check_ast(code)
        if blocked:
            names = ", ".join(blocked)
            print(f"Blocked modules detected: {names}")
            return self._json(403, {
                "success": False,
                "error": f"Blocked: {names} — not allowed in sandbox",
            })

        script = WRAPPER.format(
            code=code,
            blocked_set=repr(set(BLOCKED_MODULES)),
            blocked_sysmodules=repr(BLOCKED_SYSMODULES_KEYS),
            blocked_dunders=repr(set(BLOCKED_DUNDERS)),
            blocked_builtins=repr(BLOCKED_BUILTINS),
            max_memory=MAX_MEMORY_MB,
            max_recursion=MAX_RECURSION,
        )
        start = time.perf_counter()

        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".py", dir="/tmp")
            with os.fdopen(fd, "w") as f:
                f.write(script)
            os.chmod(tmp, 0o600)

            result = subprocess.run(
                [sys.executable, "-u", tmp],
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd="/tmp",
                env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"},
            )
            ms = int(f"{(time.perf_counter() - start) * 1000:.2f}".split(".")[0])

            stdout = result.stdout[:MAX_STDOUT].rstrip("\n")
            stderr_clean = clean_stderr(result.stderr[:MAX_STDERR]).rstrip("\n") if result.stderr else None

            if result.returncode != 0:
                error_msg = "Runtime error"
                if stderr_clean:
                    error_msg += ": " + stderr_clean.strip()
                print(f"Runtime error, exit code {result.returncode}")
                return self._json(400, {
                    "success": False,
                    "error": error_msg,
                    "stdout": stdout,
                    "exit_code": result.returncode,
                    "time_ms": ms,
                })

            return self._json(200, {
                "success": True,
                "stdout": stdout,
                "exit_code": result.returncode,
                "time_ms": ms,
            })
        except subprocess.TimeoutExpired:
            ms = int(f"{(time.perf_counter() - start) * 1000:.2f}".split(".")[0])
            print(f"Execution timed out after {timeout}s")
            return self._json(408, {
                "success": False,
                "error": f"Timed out after {timeout}s",
                "stdout": "",
                "exit_code": -1,
                "time_ms": ms,
            })
        except Exception as e:
            print(f"Execution error: {type(e).__name__}: {e}")
            return self._json(500, {
                "success": False,
                "error": "Internal server error",
            })
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except Exception as e:
                    print(f"Temp file cleanup failed: {e}")

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *a):
        pass