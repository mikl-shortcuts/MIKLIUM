import json
import os
import sys
import subprocess
import tempfile
import time
import secrets
import threading
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MAX_CODE_LENGTH = 15_000
MAX_STDIN_SIZE = 50_000
MAX_STDOUT = 10_000
MAX_STDERR = 7_000
MAX_TIMEOUT = 40
DEFAULT_TIMEOUT = 20
SEMAPHORE = threading.Semaphore(3)

temp_files = []

def cleanup_temp_files():
    for tmp_file in temp_files[:]:
        try:
            Path(tmp_file).unlink(missing_ok=True)
            temp_files.remove(tmp_file)
        except Exception as e:
            logger.error(f"Failed to clean {tmp_file}: {e}")

import atexit
atexit.register(cleanup_temp_files)

def clean_stderr(text, tmp_path):
    if not tmp_path or not text:
        return text
    return text.replace(f'File "{tmp_path}", ', 'File "<sandbox>", ')

def validate_timeout(value, default=DEFAULT_TIMEOUT, max_val=MAX_TIMEOUT):
    try:
        timeout = float(value) if isinstance(value, (int, float)) else default
        return min(max(0.5, timeout), max_val)
    except (ValueError, TypeError):
        return default

class BackendHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Sandbox-Token')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_POST(self):
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            return self._json(415, {
                "success": False,
                "error": "Content-Type must be application/json"
            })
        
        token = self.headers.get("X-Sandbox-Token")
        expected_token = os.environ.get("SANDBOX_API_KEY")
        
        if not expected_token:
            logger.error("SANDBOX_API_KEY not set in environment")
            return self._json(500, {
                "success": False,
                "error": "Server misconfigured"
            })
        
        if not token or not secrets.compare_digest(token, expected_token):
            logger.warning(f"Invalid or missing token")
            return self._json(401, {"success": False, "error": "Unauthorized"})

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._json(400, {"success": False, "error": "Invalid Content-Length"})
        
        if length == 0 or length > 100_000:
            return self._json(400, {"success": False, "error": "Empty or too large body"})

        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed: {e}")
            return self._json(400, {"success": False, "error": "Invalid JSON"})

        code = body.get("code", "")
        stdin_list = body.get("stdin", [])
        timeout = validate_timeout(body.get("timeout"))

        if not code or not isinstance(code, str):
            return self._json(400, {"success": False, "error": "Missing or invalid 'code'"})

        if len(code) > MAX_CODE_LENGTH:
            return self._json(400, {"success": False, "error": f"Code too long ({len(code)} > {MAX_CODE_LENGTH})"})

        if not isinstance(stdin_list, list):
            return self._json(400, {"success": False, "error": "'stdin' must be an array"})

        stdin_data = "\n".join(str(item) for item in stdin_list)
        if len(stdin_data) > MAX_STDIN_SIZE:
            return self._json(400, {"success": False, "error": f"stdin data too large ({len(stdin_data)} > {MAX_STDIN_SIZE})"})

        logger.info(f"Code execution: {len(code)} chars, timeout={timeout}s")

        acquired = SEMAPHORE.acquire(blocking=True, timeout=10)
        if not acquired:
            logger.warning("Server is busy, semaphore timeout")
            return self._json(503, {"success": False, "error": "Server is busy"})

        try:
            start = time.perf_counter()
            tmp = None
            try:
                tmpdir = os.environ.get("TMPDIR", "/tmp")
                fd, tmp = tempfile.mkstemp(suffix=".py", dir=tmpdir, prefix="sandbox_")
                temp_files.append(tmp)
                
                with os.fdopen(fd, "w") as f:
                    f.write(code)
                
                try:
                    os.chown(tmp, 10001, 10001)
                    os.chmod(tmp, 0o400)
                except Exception:
                    pass

                nsjail_cmd = [
                    "nsjail",
                    "-Mo",
                    "--disable_clone_newuser",
                    "--disable_clone_newns",
                    "--disable_clone_newpid",
                    "--disable_clone_newnet",
                    "--disable_clone_newipc",
                    "--disable_clone_newuts",
                    "--disable_clone_newcgroup",
                    "--user", "10001",
                    "--group", "10001",
                    "--time_limit", str(int(timeout)),
                    "--max_cpus", "1",
                    "--rlimit_as", "512",
                    "--rlimit_fsize", "0",
                    "--rlimit_nofile", "64",
                    "--rlimit_nproc", "10",
                    "--",
                    sys.executable, "-u", tmp
                ]

                result = subprocess.run(
                    nsjail_cmd,
                    input=stdin_data,
                    capture_output=True,
                    text=True,
                    timeout=timeout + 5,
                    cwd=tmpdir,
                    user=10001,
                    group=10001,
                    env={"PYTHONDONTWRITEBYTECODE": "1"}
                )
                
                ms = int((time.perf_counter() - start) * 1000)
                stdout = result.stdout[:MAX_STDOUT].rstrip("\n")
                stderr_clean = clean_stderr(result.stderr[:MAX_STDERR], tmp).rstrip("\n") if result.stderr else ""

                if result.returncode != 0:
                    logger.warning(f"Runtime error, exit code {result.returncode}")
                    error_msg = "Runtime error"
                    if stderr_clean:
                        error_msg += ": " + stderr_clean.strip()
                    return self._json(400, {
                        "success": False,
                        "error": error_msg,
                        "stdout": stdout,
                        "exit_code": result.returncode,
                        "time_ms": ms,
                    })

                logger.info(f"Code execution completed: {ms}ms")
                return self._json(200, {
                    "success": True,
                    "stdout": stdout,
                    "exit_code": result.returncode,
                    "time_ms": ms,
                })
                
            except subprocess.TimeoutExpired:
                ms = int((time.perf_counter() - start) * 1000)
                logger.warning(f"Execution timed out after {timeout}s")
                return self._json(408, {
                    "success": False,
                    "error": f"Timed out after {timeout}s",
                    "stdout": "",
                    "exit_code": -1,
                    "time_ms": ms,
                })
            except Exception as e:
                logger.exception(f"Execution error: {type(e).__name__}: {e}")
                return self._json(500, {
                    "success": False,
                    "error": f"Internal server error: {type(e).__name__}",
                })
            finally:
                if tmp and tmp in temp_files:
                    temp_files.remove(tmp)
                    try:
                        Path(tmp).unlink(missing_ok=True)
                    except Exception:
                        pass
        finally:
            SEMAPHORE.release()

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        logger.info(format % args)

def run_server():
    port = int(os.environ.get("PORT", 8000))
    server = ThreadingHTTPServer(("0.0.0.0", port), BackendHandler)
    logger.info(f"Sandbox backend running on port {port}...")
    server.serve_forever()

if __name__ == "__main__":
    run_server()