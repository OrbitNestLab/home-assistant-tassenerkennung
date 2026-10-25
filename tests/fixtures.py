import numpy as np
from PIL import Image, ImageDraw

from tassenerkennung.vision import Settings
from tassenerkennung.vessel_profiles import VesselProfiles


def settings():
    return Settings.from_dict({"camera": "camera.coffee_station"})


def scene(kind):
    image = Image.new("RGB", (1280, 720), (32, 36, 40))
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = settings().zone.pixels(image.size)
    draw.rectangle((left, top, right, bottom), fill=(82, 76, 68))
    if kind == "babybecher":
        draw.rounded_rectangle((left + 12, top + 4, right - 12, bottom - 2), 10, fill=(230, 70, 45))
        draw.rectangle((left + 20, top + 12, right - 20, top + 20), fill=(255, 225, 80))
    elif kind == "kaffeetasse":
        draw.rounded_rectangle((left + 15, top + 2, right - 10, bottom - 2), 8, fill=(40, 105, 230))
        draw.rectangle((left + 24, top + 7, right - 18, bottom - 8), fill=(215, 235, 255))
    return image


def sample(image):
    left, top, right, bottom = settings().zone.pixels(image.size)
    patch = image.crop((left, top, right, bottom)).resize((26, 28), Image.Resampling.BILINEAR)
    return np.asarray(patch, dtype=np.uint8).tolist()


def profiles():
    empty = sample(scene("leer"))
    baby = sample(scene("babybecher"))
    coffee = sample(scene("kaffeetasse"))
    data = {
        "version": 2,
        "camera": "camera.coffee_station",
        "source_size": [1280, 720],
        "zone_relative_box": [0, 0, 1, 1],
        "names": {"babybecher": "Babybecher", "kaffeetasse": "Kaffeetasse"},
        "samples": {"leer": [empty] * 3, "babybecher": [baby] * 3, "kaffeetasse": [coffee] * 3},
    }
    return VesselProfiles(data)
