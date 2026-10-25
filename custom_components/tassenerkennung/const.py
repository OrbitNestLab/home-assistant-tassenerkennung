"""Configuration for local cup recognition."""

DOMAIN = "tassenerkennung"
VERSION = "0.6.0"
MODEL_SHA256 = "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"
DEFAULTS = {
    "camera": "camera.coffee_station",
    "interval": 1,
    "confidence": 0.75,
    "confirmations": 5,
    "crop_left": 40.0,
    "crop_top": 40.0,
    "crop_right": 57.0,
    "crop_bottom": 70.0,
    "zone_left": 47.5,
    "zone_top": 56.5,
    "zone_right": 53.5,
    "zone_bottom": 65.0,
    "anchor_left": 43.0,
    "anchor_top": 45.0,
    "anchor_right": 53.0,
    "anchor_bottom": 55.0,
}
