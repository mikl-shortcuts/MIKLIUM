import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Add the models directory to path
project_root = os.getcwd()
models_path = os.path.join(project_root, "api/models/miklium-lm-nano")
if models_path not in sys.path:
    sys.path.insert(0, models_path)

try:
    import inference
except ImportError:
    # Fallback to relative path if os.getcwd() isn't what we expect
    models_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../models/miklium-lm-nano")
    if models_path not in sys.path:
        sys.path.insert(0, models_path)
    import inference

# Initialize the model once
MODEL_FILE = os.path.join(models_path, "website/miklium-lm-nano_502.7K.miklium_model")
model = None

try:
    model = inference.MikliumNanoInference(MODEL_FILE)
except Exception as e:
    print(f"Error loading model: {e}")

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if not content_length:
            return self._send_json(400, {"success": False, "error": "Missing body"})

        try:
            body = json.loads(self.rfile.read(content_length))
        except Exception:
            return self._send_json(400, {"success": False, "error": "Invalid JSON"})

        model_name = body.get("model", "")
        user_input = body.get("input", "")

        if not user_input:
            return self._send_json(400, {"success": False, "error": "Missing 'input' field"})

        if model is None:
            return self._send_json(500, {"success": False, "error": "Model not loaded"})

        try:
            # Simple prompt engineering for the nano model if needed
            # The model was trained with <user> and <ai> tags
            prompt = f"<user> {user_input} <ai>"
            
            output_text = model.generate(prompt, length=200, temp=0.7)
            
            # Clean up the output tags if any
            output_text = output_text.replace("<ai>", "").replace("<user>", "").replace("<eos>", "").strip()

            return self._send_json(200, {
                "success": True,
                "model": "miklium-lm-nano",
                "output_text": output_text,
                "usage": {
                    "total_tokens": len(user_input.split()) + len(output_text.split()) # Rough estimate
                }
            })
        except Exception as e:
            return self._send_json(500, {"success": False, "error": f"Inference failed: {str(e)}"})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, *a):
        pass
