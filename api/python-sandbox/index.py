import json
import os
import ast
import urllib.request
from urllib.error import HTTPError, URLError
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VERCEL_ENV = os.environ.get("VERCEL_ENV", "preview")

PROD_URL = "https://miklium.onrender.com"
PREVIEW_URL = "https://miklium-preview.onrender.com"

if VERCEL_ENV == "production":
    SANDBOX_URL = PROD_URL
else:
    SANDBOX_URL = PREVIEW_URL

MAX_CODE_LENGTH = 15_000
MAX_STDIN_SIZE = 50_000
DEFAULT_TIMEOUT = 20
MAX_TIMEOUT = 40

def format_json(data):
    return json.dumps(data, indent=2, separators=(',', ' : '), ensure_ascii=False).encode('utf-8')

def make_response(start_response, status, body_dict):
    body_bytes = format_json(body_dict)
    start_response(status, [
        ('Content-Type', 'application/json; charset=utf-8'),
        ('Access-Control-Allow-Origin', '*'),
        ('X-Content-Type-Options', 'nosniff'),
    ])
    return [body_bytes]

def validate_timeout(value, default=DEFAULT_TIMEOUT, max_val=MAX_TIMEOUT):
    try:
        timeout = float(value) if isinstance(value, (int, float)) else default
        return min(max(0.5, timeout), max_val)
    except (ValueError, TypeError):
        return default

def check_ast(code):
    try:
        ast.parse(code)
        return []
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]

def process_backend_response(resp_bytes):
    try:
        data = json.loads(resp_bytes.decode('utf-8'))
        
        is_success = data.get("success", False)
        exit_code = data.get("exit_code", 0)
        stdout = data.get("stdout", "")
        stderr = data.get("stderr", "")
        time_ms = data.get("time_ms", 0)
        error_msg = data.get("error", "")

        if is_success and exit_code == 0:
            return {
                "success": True,
                "exit_code": 0,
                "stdout": stdout,
                "time_ms": time_ms
            }
        elif exit_code != 0 or stderr or stdout:
            if not error_msg and stderr:
                error_msg = f"Runtime error: {stderr}"
            elif not error_msg:
                error_msg = f"Runtime error: Exit code {exit_code}"
            
            return {
                "success": False,
                "stdout": stdout,
                "exit_code": exit_code,
                "time_ms": time_ms,
                "error": error_msg
            }
        else:
            return {
                "success": False,
                "error": error_msg or "Execution failed"
            }
    except Exception:
        fallback_text = resp_bytes.decode('utf-8', errors='ignore').strip()
        return {
            "success": False,
            "error": fallback_text or "Invalid response from execution sandbox"
        }

def app(environ, start_response):
    request_method = environ.get('REQUEST_METHOD', '')
    
    if request_method == 'OPTIONS':
        start_response('204 No Content', [
            ('Access-Control-Allow-Origin', '*'),
            ('Access-Control-Allow-Methods', 'POST, OPTIONS'),
            ('Access-Control-Allow-Headers', 'Content-Type'),
        ])
        return [b'']

    if request_method != 'POST':
        logger.warning(f"Rejected {request_method} request")
        return make_response(start_response, '405 Method Not Allowed', {
            "success": False,
            "error": "Only POST method is supported"
        })

    content_type = environ.get('CONTENT_TYPE', '')
    if 'application/json' not in content_type:
        return make_response(start_response, '415 Unsupported Media Type', {
            "success": False,
            "error": "Content-Type must be application/json"
        })

    try:
        request_body_size = int(environ.get('CONTENT_LENGTH', 0))
    except ValueError:
        request_body_size = 0

    if request_body_size == 0 or request_body_size > 100_000:
        return make_response(start_response, '400 Bad Request', {
            "success": False,
            "error": "Empty or too large body"
        })

    try:
        request_body = environ['wsgi.input'].read(request_body_size)
        body = json.loads(request_body.decode('utf-8'))
    except Exception as e:
        logger.error(f"JSON parse failed: {e}")
        return make_response(start_response, '400 Bad Request', {
            "success": False,
            "error": "Invalid JSON"
        })

    code = body.get("code", "")
    stdin_list = body.get("stdin", [])
    timeout = validate_timeout(body.get("timeout"))

    if not code or not isinstance(code, str):
        return make_response(start_response, '400 Bad Request', {
            "success": False,
            "error": "Missing 'code'"
        })

    if len(code) > MAX_CODE_LENGTH:
        return make_response(start_response, '400 Bad Request', {
            "success": False,
            "error": f"Code too long ({len(code)} > {MAX_CODE_LENGTH})"
        })

    if not isinstance(stdin_list, list):
        return make_response(start_response, '400 Bad Request', {
            "success": False,
            "error": "'stdin' must be an array"
        })

    stdin_data = "\n".join(str(item) for item in stdin_list)
    if len(stdin_data) > MAX_STDIN_SIZE:
        return make_response(start_response, '400 Bad Request', {
            "success": False,
            "error": f"stdin data too large ({len(stdin_data)} > {MAX_STDIN_SIZE})"
        })

    blocked = check_ast(code)
    if blocked:
        return make_response(start_response, '400 Bad Request', {
            "success": False,
            "error": blocked[0]
        })

    secret_key = os.environ.get("SANDBOX_API_KEY")
    if not secret_key:
        logger.error("SANDBOX_API_KEY not configured")
        return make_response(start_response, '500 Internal Server Error', {
            "success": False,
            "error": "Backend credentials misconfigured"
        })

    req_data = json.dumps({
        "code": code,
        "stdin": stdin_list,
        "timeout": timeout
    }).encode('utf-8')

    req = urllib.request.Request(
        SANDBOX_URL,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "X-Sandbox-Token": secret_key
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout + 15) as response:
            resp_data = response.read()
            standardized_body = process_backend_response(resp_data)
            return make_response(start_response, f'{response.status} OK', standardized_body)
    except HTTPError as e:
        resp_data = e.read()
        standardized_body = process_backend_response(resp_data)
        return make_response(start_response, f'{e.code} Error', standardized_body)
    except URLError as e:
        logger.error(f"Failed to connect to backend: {e}")
        return make_response(start_response, '503 Service Unavailable', {
            "success": False,
            "error": "Backend service unavailable"
        })
    except Exception as e:
        logger.exception(f"Execution error: {type(e).__name__}")
        return make_response(start_response, '500 Internal Server Error', {
            "success": False,
            "error": "Internal server error"
        })