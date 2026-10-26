"""HA-independent local detector, geometry, position guard and annotation.

YOLOX preprocessing/decoding follows Megvii's Apache-2.0 reference implementation.
This module never controls the coffee machine and never sends images off-device.
"""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .const import DEFAULTS, MODEL_SHA256


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self):
        if not all(math.isfinite(v) for v in self.tuple):
            raise ValueError("invalid_area")
        if not (0 <= self.left < self.right <= 1 and 0 <= self.top < self.bottom <= 1):
            raise ValueError("invalid_area")

    @property
    def tuple(self):
        return self.left, self.top, self.right, self.bottom

    def pixels(self, size):
        w, h = size
        return tuple(round(v * d) for v, d in zip(self.tuple, (w, h, w, h)))

    def contains(self, x, y):
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def encloses(self, other):
        return self.contains(other.left, other.top) and self.contains(other.right, other.bottom)


@dataclass(frozen=True)
class Settings:
    camera: str
    interval: int
    confidence: float
    confirmations: int
    crop: Rect
    zone: Rect
    anchor: Rect

    @classmethod
    def from_dict(cls, values):
        data = {**DEFAULTS, **values}
        def rect(name):
            return Rect(*(float(data[f"{name}_{side}"]) / 100 for side in ("left", "top", "right", "bottom")))
        settings = cls(str(data["camera"]), int(data["interval"]), float(data["confidence"]),
                       int(data["confirmations"]), rect("crop"), rect("zone"), rect("anchor"))
        if not 3 <= settings.interval <= 60 or not .4 <= settings.confidence <= .95 or not 2 <= settings.confirmations <= 8:
            raise ValueError("invalid_settings")
        if not settings.crop.encloses(settings.zone) or not settings.crop.encloses(settings.anchor):
            raise ValueError("areas_outside_crop")
        if min(settings.zone.right-settings.zone.left, settings.zone.bottom-settings.zone.top) < .01:
            raise ValueError("area_too_small")
        # The reference must not include the cup, which is expected to come and go.
        overlap_w = min(settings.zone.right, settings.anchor.right) - max(settings.zone.left, settings.anchor.left)
        overlap_h = min(settings.zone.bottom, settings.anchor.bottom) - max(settings.zone.top, settings.anchor.top)
        if overlap_w > 0 and overlap_h > 0:
            raise ValueError("reference_overlaps_zone")
        return settings

    @property
    def fingerprint(self):
        value = (self.camera, self.crop.tuple, self.zone.tuple, self.anchor.tuple)
        return sha256(json.dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]  # Original full-frame pixels.
    score: float
    class_id: int = 41

    def in_zone(self, zone, size):
        x1, y1, x2, y2 = self.box
        w, h = size
        # Require both the centre and bottom centre of the vessel under the outlet.
        return zone.contains((x1+x2)/2/w, (y1+y2)/2/h) and zone.contains((x1+x2)/2/w, y2/h)


def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0, x2-x1) * max(0, y2-y1)
    total = max(0, a[2]-a[0])*max(0, a[3]-a[1]) + max(0, b[2]-b[0])*max(0, b[3]-b[1]) - overlap
    return overlap / total if total else 0


def covers_zone(box, zone):
    overlap = max(0, min(box[2], zone[2])-max(box[0], zone[0])) * max(0, min(box[3], zone[3])-max(box[1], zone[1]))
    area = (zone[2]-zone[0])*(zone[3]-zone[1])
    return overlap / area if area else 0


def font(size):
    return ImageFont.truetype(str(Path(__file__).parent / "Roboto.ttf"), size)


class Confirmation:
    """Consecutive observations of the same cup; removal invalidates immediately."""

    def __init__(self, required, max_gap):
        self.required, self.max_gap = required, max_gap
        self.reset()

    def reset(self):
        self.count = 0
        self.previous = None
        self.last_at = None

    def update(self, candidate, timestamp, allowed=True):
        if not allowed or candidate is None:
            self.reset()
            return False
        if self.last_at is not None and (timestamp <= self.last_at or timestamp - self.last_at > self.max_gap):
            self.reset()
        if self.previous and iou(self.previous.box, candidate.box) < .5:
            self.reset()
        self.count += 1
        self.previous, self.last_at = candidate, timestamp
        return self.count >= self.required


class Detector:
    def __init__(self, model_path):
        # Imported in the executor; the native runtime may take time to load.
        import onnxruntime as ort
        path = Path(model_path)
        if sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("model_checksum")
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        self.input_size = self.session.get_inputs()[0].shape[-1]
        model_input=self.session.get_inputs()[0]
        self.input_name=model_input.name
        shape=model_input.shape
        self.input_height,self.input_width=int(shape[-2]),int(shape[-1])
        if min(self.input_height,self.input_width)<128 or max(self.input_height,self.input_width)>1280:
            raise ValueError('unsupported_model_input')
        self.grids, self.strides = [], []
        for stride in (8, 16, 32):
            y, x = np.meshgrid(np.arange(self.input_size//stride), np.arange(self.input_size//stride), indexing="ij")
            self.grids.append(np.stack((x, y), axis=-1).reshape(-1, 2))
            self.strides.append(np.full((x.size, 1), stride))
        self.grids, self.strides = np.concatenate(self.grids), np.concatenate(self.strides)

    def detect(self, image, settings):
        left, top, right, bottom = settings.crop.pixels(image.size)
        crop = image.crop((left, top, right, bottom))
        w, h = crop.size
        ratio = min(self.input_size/w, self.input_size/h)
        resized = np.asarray(crop.resize((max(1, int(w*ratio)), max(1, int(h*ratio))), Image.Resampling.BILINEAR))[:, :, ::-1]
        padded = np.full((self.input_size, self.input_size, 3), 114, dtype=np.uint8)
        padded[:resized.shape[0], :resized.shape[1]] = resized
        tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
        output = self.session.run(None, {self.input_name: tensor})[0][0].copy()
        output[:, :2] = (output[:, :2] + self.grids) * self.strides
        output[:, 2:4] = np.exp(np.clip(output[:, 2:4], -20, 20)) * self.strides
        scores = output[:, 4, None] * output[:, 5:]
        classes, best = scores.argmax(axis=1), scores.max(axis=1)
        # Person detection is retained as an occlusion warning; other objects cannot pass as cups.
        indices = np.where(np.isin(classes, [0,39,40,41]) & (best >= settings.confidence))[0]
        found = []
        for index in sorted(indices, key=lambda i: float(best[i]), reverse=True):
            cx, cy, bw, bh = output[index, :4] / ratio
            bx1, by1, bx2, by2 = cx-bw/2, cy-bh/2, cx+bw/2, cy+bh/2
            # Reject boxes clipped by the crop or too small to assess.
            if int(classes[index])==0:
                bx1,by1,bx2,by2=max(0,bx1),max(0,by1),min(w,bx2),min(h,by2)
                if bx2-bx1<6 or by2-by1<6:continue
            elif bx1 < 1 or by1 < 1 or bx2 > w-1 or by2 > h-1 or bw < 10 or bh < 12:
                continue
            det = Detection((float(bx1+left), float(by1+top), float(bx2+left), float(by2+top)), float(best[index]), int(classes[index]))
            if not any((d.class_id == det.class_id or (d.class_id in {39,40,41} and det.class_id in {39,40,41})) and iou(d.box, det.box) > .45 for d in found):
                found.append(det)
        return found


def open_image(content):
    with Image.open(BytesIO(content)) as raw:
        if raw.width * raw.height > 16_000_000 or min(raw.size) < 128:
            raise ValueError("invalid_image")
        return ImageOps.exif_transpose(raw).convert("RGB")


def gray_frame(image):
    width = min(640, image.width)
    return np.asarray(image.convert("L").resize((width, round(image.height*width/image.width)), Image.Resampling.BILINEAR), dtype=np.float32)


def make_reference(image, settings):
    gray = gray_frame(image)
    size = (gray.shape[1], gray.shape[0])
    l, t, r, b = settings.anchor.pixels(size)
    patch = gray[t:b, l:r]
    if patch.size < 100 or patch.std() < 8:
        raise ValueError("reference_not_distinct")
    return {"fingerprint": settings.fingerprint, "source_size": list(image.size),
            "patch": patch.astype(np.uint8).tolist(), "rect": [l, t, r, b]}


@dataclass(frozen=True)
class Alignment:
    ok: bool
    reason: str
    similarity: float = 0
    shift: tuple[int, int] = (0, 0)


def check_alignment(image, settings, reference):
    if not reference or reference.get("fingerprint") != settings.fingerprint:
        return Alignment(False, "Ausrichtung bestätigen")
    if reference.get("source_size") != list(image.size):
        return Alignment(False, "Bildgröße geändert – Ausrichtung prüfen")
    gray = gray_frame(image)
    ref = np.asarray(reference["patch"], dtype=np.float32)
    ref = ref - ref.mean()
    norm = np.linalg.norm(ref)
    if norm < 1:
        return Alignment(False, "Positionsreferenz unbrauchbar")
    l, t, r, b = reference["rect"]
    best, best_shift = -1., (0, 0)
    # Look nearby for the machine; never silently move the accepted cup zone.
    for dy in range(-12, 13):
        for dx in range(-18, 19):
            if l+dx < 0 or t+dy < 0 or r+dx > gray.shape[1] or b+dy > gray.shape[0]:
                continue
            patch = gray[t+dy:b+dy, l+dx:r+dx]
            patch = patch - patch.mean()
            score = float(np.sum(ref * patch) / (norm * max(float(np.linalg.norm(patch)), 1)))
            if score > best:
                best, best_shift = score, (dx, dy)
    if best < .85:
        return Alignment(False, "Maschine verdeckt oder Ausrichtung unsicher", best, best_shift)
    if abs(best_shift[0]) > 2 or abs(best_shift[1]) > 2:
        return Alignment(False, "Maschine verschoben – Ausrichtung prüfen", best, best_shift)
    return Alignment(True, "Position bestätigt", best, best_shift)


@dataclass
class Observation:
    present: bool | None
    status: str
    confidence: float
    confirmations: int
    detections: list[Detection]
    alignment: Alignment
    image_size: tuple[int, int]
    inference_ms: float


class Vision:
    def __init__(self, detector, settings):
        self.detector, self.settings = detector, settings
        self.confirmation = Confirmation(settings.confirmations, settings.interval * 2.5)

    def evaluate(self, content, reference, timestamp=None):
        started = time.monotonic()
        image = open_image(content)
        alignment = check_alignment(image, self.settings, reference)
        region = np.asarray(image.crop(self.settings.crop.pixels(image.size)).convert("L"))
        if region.std() < 8 or np.percentile(region, 95) < 24:
            alignment = Alignment(False, "Bild zu dunkel oder verdeckt")
        detections = self.detector.detect(image, self.settings)
        cups = [d for d in detections if d.class_id == 41 and d.in_zone(self.settings.zone, image.size)]
        zone = self.settings.zone.pixels(image.size)
        blocked = any(d.class_id == 0 and covers_zone(d.box, zone) > .1 for d in detections)
        allowed = alignment.ok and not blocked and len(cups) == 1
        present = self.confirmation.update(cups[0] if len(cups) == 1 else None, timestamp if timestamp is not None else time.monotonic(), allowed)
        if not alignment.ok:
            status, value = alignment.reason, None
        elif blocked:
            status, value = "Prüfbereich verdeckt", None
        elif len(cups) > 1:
            status, value = "Mehrere Tassen im Prüfbereich", None
        elif not cups:
            status, value = "Keine Tasse unter dem Auslauf", False
        elif present:
            status, value = "Tasse unter dem Auslauf erkannt", True
        else:
            status, value = "Tasse wird geprüft", False
        observation = Observation(value, status, max((d.score for d in cups), default=0), self.confirmation.count,
                                  detections, alignment, image.size, (time.monotonic()-started)*1000)
        return observation, render(image, self.settings, observation)


def render(image, settings, observation=None, message=None):
    """Return only the machine crop, not the surrounding kitchen."""
    l, t, r, b = settings.crop.pixels(image.size)
    crop = image.crop((l, t, r, b))
    scale = min(3., 900/crop.width)
    crop = crop.resize((round(crop.width*scale), round(crop.height*scale)), Image.Resampling.BICUBIC)
    draw = ImageDraw.Draw(crop)
    def box(rect):
        return tuple(round((v-origin)*scale) for v, origin in zip(rect, (l, t, l, t)))
    # Blue is always the target area, never a positive detection.
    draw.rectangle(box(settings.zone.pixels(image.size)), outline="#7fa7c5", width=2)
    if observation and not message:
        for detection in observation.detections:
            if detection.class_id != 41:
                continue
            inside = detection.in_zone(settings.zone, image.size)
            color = "#61c99a" if inside and observation.present else "#e1b567" if inside else "#93a3b6"
            draw.rectangle(box(detection.box), outline=color, width=4)
        message = observation.status
    message = message or "Bild noch nicht verfügbar"
    text_font = font(16)
    header = Image.new("RGB", (crop.width, 56), "#182637")
    d = ImageDraw.Draw(header)
    # Fit the status to the actual output width.
    while len(message) > 8 and d.textbbox((0, 0), message, font=text_font)[2] > crop.width-24:
        text_font = font(max(10, text_font.size-1))
        if text_font.size <= 10:
            break
    d.text((12, 9), message, font=text_font, fill="#eef2f6")
    d.text((12, 32), "Testbetrieb · keine Kaffeeausgabe", font=font(12), fill="#b6c3d2")
    result = Image.new("RGB", (crop.width, crop.height+56))
    result.paste(header, (0, 0)); result.paste(crop, (0, 56))
    output = BytesIO(); result.save(output, format="JPEG", quality=88)
    return output.getvalue()


def placeholder(message="Warte auf Kamerabild"):
    image = Image.new("RGB", (640, 480), "#162334")
    draw = ImageDraw.Draw(image)
    draw.text((28, 200), message, font=font(20), fill="#e6edf5")
    draw.text((28, 240), "Keine Freigabe · Testbetrieb", font=font(15), fill="#aebed0")
    output = BytesIO(); image.save(output, format="JPEG")
    return output.getvalue()
