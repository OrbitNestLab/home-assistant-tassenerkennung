"""Configuration for local cup recognition."""

DOMAIN = "tassenerkennung"
VERSION = "0.7.0"
MODEL_SHA256 = "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"
DEFAULTS = {
    "camera": "camera.eve_cam_07b0",
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
