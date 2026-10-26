"""Authenticated access to the local HA app; only the machine crop is sent."""

from io import BytesIO
import base64
import json
from pathlib import Path
import urllib.request

from .vision import Detection


class LocalDetector:
    def __init__(self, config_path):
        config = json.loads(Path(config_path).read_text())
        self.url, self.token = config["url"].rstrip("/"), config["token"]
        if not self.url.startswith("http://local-tassenerkennung:") or len(self.token) < 32:
            raise ValueError("invalid_local_backend")

    def detect(self, image, settings):
        left, top, right, bottom = settings.crop.pixels(image.size)
        crop = image.crop((left, top, right, bottom))
        content = BytesIO(); crop.save(content, format="PNG")
        request = urllib.request.Request(self.url + "/detect?confidence=0.400",
            data=content.getvalue(), headers={"Authorization": "Bearer " + self.token, "Content-Type": "image/png"})
        # Internal service must not be routed via an inherited HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=8) as response:
            result = json.load(response)
        if result.get("protocol") != 1:
            raise ValueError("backend_protocol")
        detections = []
        for item in result["detections"]:
            x1, y1, x2, y2 = item["box"]
            detections.append(Detection((x1+left, y1+top, x2+left, y2+top), float(item["score"]), int(item["class_id"])))
        return detections

    def register_camera(self, content, reference, candidate=None):
        payload={"reference":reference,"image":base64.b64encode(content).decode()}
        if candidate is not None:payload["candidate"]=candidate
        request=urllib.request.Request(self.url+"/register",data=json.dumps(payload).encode(),
            headers={"Authorization":"Bearer "+self.token,"Content-Type":"application/json"})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=12) as response:result=json.load(response)
        if result.get("protocol")!=2:raise ValueError("camera_backend_protocol")
        return result["registration"]

    def track_machine(self, content, reference, settings, previous=None):
        payload={'reference':reference,'image':base64.b64encode(content).decode(),
                 'anchor':list(settings.anchor.tuple),'previous':previous}
        request=urllib.request.Request(self.url+'/track',data=json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=10) as response:result=json.load(response)
        if result.get('protocol')!=3:raise ValueError('tracking_backend_protocol')
        return result

    def setup_machine(self,reference,body):
        request=urllib.request.Request(self.url+'/machine-setup',data=json.dumps({'reference':reference,'body':body}).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request,timeout=15) as response:result=json.load(response)
        except urllib.error.HTTPError as error:
            raise ValueError('Der Auslauf konnte im markierten Maschinenrahmen nicht erkannt werden. Bitte enger markieren und beide Düsen sichtbar lassen.') from error
        if result.get('protocol')!=4:raise ValueError('setup_backend_protocol')
        return result['model']
