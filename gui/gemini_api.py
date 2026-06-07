"""
Job HTTP API for the Gemini automation engine.

Uses the stdlib ThreadingHTTPServer (zero new deps, no clash with the Qt event
loop) in its own daemon thread. The handler only touches GeminiEngine, which is
thread-safe; all Playwright work happens on the worker threads.

Endpoints
  POST /v1/jobs            {type, prompt, input_media:[{data,media_type}], account?}
                           → 200 {job_id, status}
  GET  /v1/jobs/{job_id}   → 200 job.public()  | 404
  GET  /v1/pool            → 200 engine.pool_status()
  GET  /health             → 200 {status, pool}
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

ENGINE_HOST = os.environ.get("GEMINI_ENGINE_HOST", "127.0.0.1")
ENGINE_PORT = int(os.environ.get("GEMINI_ENGINE_PORT", "8090"))


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # silence default stderr logging
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if path == "/health":
                return self._send(200, {"status": "ok", "pool": engine.pool_status()})
            if path == "/v1/pool":
                return self._send(200, engine.pool_status())
            if path.startswith("/v1/jobs/"):
                job_id = path[len("/v1/jobs/"):]
                job = engine.get(job_id)
                if not job:
                    return self._send(404, {"error": "job not found"})
                return self._send(200, job.public())
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            if path != "/v1/jobs":
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length) or b"{}")
            except Exception as e:
                return self._send(400, {"error": f"bad json: {e}"})
            jtype = data.get("type", "image")
            if jtype not in ("image", "video", "dump"):
                return self._send(400, {"error": "type must be image|video|dump"})
            if jtype != "dump" and not data.get("prompt"):
                return self._send(400, {"error": "prompt is required"})
            job = engine.submit(
                type=jtype,
                prompt=data.get("prompt", ""),
                input_media=data.get("input_media", []),
                account=data.get("account"),
            )
            return self._send(200, {"job_id": job.id, "status": job.status})

    return Handler


def start_api_server(engine, host=ENGINE_HOST, port=ENGINE_PORT):
    server = ThreadingHTTPServer((host, port), make_handler(engine))
    server.daemon_threads = True
    Thread(target=server.serve_forever, name="gemini-api", daemon=True).start()
    print(f"[GeminiEngine] job API listening on http://{host}:{port}")
    return server
