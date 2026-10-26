"""Local model API: no image storage, external calls or device commands."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit

from visionlib.vision import Detector, Settings, open_image
from visionlib.registration import register_camera, verify_camera
from visionlib.machine import bootstrap, track, setup
from web import HTML, HomeAssistantClient, ActionJobs


def create_handler(detector, key, ha=None):
    inference_lock=threading.Lock()
    tracking_lock=threading.Lock()
    jobs=ActionJobs(ha) if ha else None
    class Handler(BaseHTTPRequestHandler):
        server_version = "Tassenerkennung/0.4.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(8)

        def log_message(self, *args):
            pass  # Never log camera payloads or authentication headers.

        def respond(self, status, result):
            content = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def respond_bytes(self,status,content,content_type):
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(content)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.end_headers();self.wfile.write(content)

        def ingress(self):
            return bool(self.headers.get("X-Ingress-Path"))

        def do_GET(self):
            path=urlsplit(self.path).path
            if path == "/health":
                self.respond(200, {"ready": True, "version": "0.4.1"})
            elif path in ("/","/index.html") and self.ingress():
                self.respond_bytes(200,HTML.encode(),"text/html; charset=utf-8")
            elif path == "/app/status" and self.ingress() and ha:
                try:self.respond(200,{**ha.status(),**jobs.snapshot()})
                except Exception:self.respond(503,{"error":"home_assistant_unavailable"})
            elif path == "/app/camera" and self.ingress() and ha:
                try:
                    content,content_type=ha.camera();self.respond_bytes(200,content,content_type)
                except Exception:self.respond(503,{"error":"camera_unavailable"})
            elif path == "/app/source-preview" and self.ingress() and ha:
                try:
                    content,content_type=ha.camera(source=True);self.respond_bytes(200,content,content_type)
                except Exception:self.respond(503,{"error":"camera_unavailable"})
            elif path == "/app/tracking" and self.ingress() and ha:
                try:
                    content,content_type=ha.camera(tracking=True);self.respond_bytes(200,content,content_type)
                except Exception:self.respond(503,{"error":"camera_unavailable"})
            elif path == '/app/setup-preview' and self.ingress() and ha:
                try:
                    content,content_type=ha.camera(raw=True);self.respond_bytes(200,content,content_type)
                except Exception:self.respond(503,{'error':'camera_unavailable'})
            else:
                self.respond(404, {"error": "not_found"})

        def do_POST(self):
            path=urlsplit(self.path).path
            if path=="/app/action":
                if not self.ingress() or not ha or self.headers.get("X-Tassenerkennung-App")!="1":
                    self.respond(403,{"error":"forbidden"});return
                if self.headers.get("Content-Length") is None:
                    self.respond(411,{"error":"content_length_required"})
                    self.close_connection=True
                    return
                try:
                    length=int(self.headers.get("Content-Length","0"))
                    if not 1<=length<=4096:raise ValueError("request_size")
                    payload=json.loads(self.rfile.read(length))
                    if not isinstance(payload,dict):raise ValueError('Ungültige Anfrage')
                    self.respond(202,jobs.submit(payload))
                except RuntimeError as error:self.respond(409,{"error":"action_running","message":str(error)[:250]})
                except (ValueError,json.JSONDecodeError) as error:self.respond(400,{"error":"invalid_action","message":str(error)[:250]})
                except Exception:self.respond(503,{"error":"home_assistant_unavailable"})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + key):
                self.respond(401, {"error": "unauthorized"})
                self.close_connection = True
                return
            if path not in ("/detect","/register","/track","/machine-setup"):
                self.respond(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 3_000_000:
                    self.respond(413, {"error": "image_size"}); return
                if path=='/machine-setup':
                    payload=json.loads(self.rfile.read(length))
                    with tracking_lock:model=setup(payload['reference'],payload['body'])
                    self.respond(200,{'protocol':4,'model':model});return
                if path=="/track":
                    payload=json.loads(self.rfile.read(length))
                    reference=payload['reference']
                    with tracking_lock:
                        model=reference.get('machine_model') or bootstrap(reference,payload['anchor'])
                        result=track(reference,payload['image'],model,payload.get('previous'))
                    self.respond(200,{'protocol':3,'tracking':result,'model':model})
                    return
                if path=="/register":
                    content=self.rfile.read(length)
                    if len(content)!=length:raise ValueError("incomplete_image")
                    payload=json.loads(content)
                    reference=payload["reference"];image=payload["image"]
                    result=verify_camera(reference,image,payload["candidate"]) if payload.get("candidate") is not None else register_camera(reference,image)
                    self.respond(200,{"protocol":2,"registration":result})
                    return
                confidence = float(parse_qs(urlsplit(self.path).query).get("confidence", [".6"])[0])
                settings = Settings.from_dict({"crop_left": 0, "crop_top": 0, "crop_right": 100,
                                              "crop_bottom": 100, "confidence": confidence})
                content = self.rfile.read(length)
                if len(content) != length:
                    raise ValueError("incomplete_image")
                image = open_image(content)
                started = time.monotonic()
                with inference_lock:found = detector.detect(image, settings)
                self.respond(200, {"protocol": 1, "model_ms": round((time.monotonic()-started)*1000, 1),
                    "detections": [{"box": d.box, "score": d.score, "class_id": d.class_id} for d in found]})
            except (ValueError, OSError, TimeoutError):
                self.respond(400, {"error": "invalid_image_or_settings"})
            except Exception:
                self.respond(503, {"error": "inference_unavailable"})
    return Handler


def main():
    key = json.loads(Path("/data/options.json").read_text())["api_key"]
    if len(key) < 32:
        raise ValueError("Bitte einen gültigen lokalen API-Schlüssel konfigurieren.")
    detector = Detector(Path("/app/modelle/yolox_s.onnx"))
    ha=HomeAssistantClient()
    print("Tassenerkennung App 0.4.1 bereit. Maschinenverfolgung und geführter Einlernassistent.", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), create_handler(detector, key, ha)).serve_forever()


if __name__ == "__main__":
    main()
