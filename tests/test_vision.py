from dataclasses import replace
from io import BytesIO

import numpy as np
from PIL import Image
import pytest

from tassenerkennung.vision import (
    Confirmation, Detection, Rect, Settings, Vision, check_alignment, make_reference,
)


@pytest.fixture
def settings():
    return Settings.from_dict({})


@pytest.fixture
def image():
    # A textured machine front provides a unique reference independent of the cup zone.
    rng = np.random.default_rng(7)
    return Image.fromarray(rng.integers(25, 230, (720, 1280, 3), dtype=np.uint8))


def jpeg(image):
    data = BytesIO(); image.save(data, format="PNG")
    return data.getvalue()


def cup(settings, image, shift=0):
    x1, y1, x2, y2 = settings.zone.pixels(image.size)
    return Detection((x1+10+shift, y1+4, x2-10+shift, y2-3), .9)


class FakeDetector:
    def __init__(self, detections):
        self.detections = detections

    def detect(self, image, settings):
        return self.detections


def test_area_validation(settings):
    with pytest.raises(ValueError): Rect(.6, .2, .4, .5)
    with pytest.raises(ValueError): Rect(float("nan"), 0, 1, 1)
    with pytest.raises(ValueError): Settings.from_dict({"zone_right": 90})
    with pytest.raises(ValueError): Settings.from_dict({"anchor_bottom": 60})
    assert settings.crop.encloses(settings.zone)


def test_reference_is_brightness_tolerant_and_blocks_movement(settings, image):
    reference = make_reference(image, settings)
    assert check_alignment(image, settings, reference).ok
    brighter = Image.fromarray(np.clip(np.asarray(image, dtype=np.int16)+10, 0, 255).astype(np.uint8))
    assert check_alignment(brighter, settings, reference).ok
    shifted = Image.new("RGB", image.size)
    shifted.paste(image, (16, 0))
    alignment = check_alignment(shifted, settings, reference)
    assert not alignment.ok
    assert "verschoben" in alignment.reason
    assert alignment.shift[0] == 8
    assert not check_alignment(image, settings, None).ok
    assert not check_alignment(image.resize((640, 360)), settings, reference).ok


def test_low_information_reference_is_rejected(settings):
    with pytest.raises(ValueError, match="reference_not_distinct"):
        make_reference(Image.new("RGB", (1280, 720), "gray"), settings)


def test_cup_must_be_under_outlet_not_elsewhere(settings, image):
    assert cup(settings, image).in_zone(settings.zone, image.size)
    assert not cup(settings, image, shift=100).in_zone(settings.zone, image.size)


def test_temporal_confirmation_resets_on_removal_gap_or_object_change(settings, image):
    confirm = Confirmation(3, 12.5)
    detected = cup(settings, image)
    assert not confirm.update(detected, 1)
    assert not confirm.update(detected, 6)
    assert confirm.update(detected, 11)
    assert not confirm.update(None, 16)
    assert not confirm.update(detected, 21)
    assert not confirm.update(detected, 50)
    assert confirm.count == 1
    assert not confirm.update(cup(settings, image, shift=200), 55)
    assert confirm.count == 1
    assert not confirm.update(detected, 60, allowed=False)
    assert confirm.count == 0


def test_end_to_end_confirm_remove_and_uncalibrated(settings, image):
    detected = cup(settings, image)
    detector = FakeDetector([detected])
    vision = Vision(detector, settings)
    reference = make_reference(image, settings)
    content = jpeg(image)
    observation, annotated = vision.evaluate(content, None, 1)
    assert observation.present is None
    assert annotated.startswith(b"\xff\xd8")
    for timestamp, expected in [(6, False), (7, False), (8, False), (9, False), (10, True), (11, True)]:
        observation, _ = vision.evaluate(content, reference, timestamp)
        assert observation.present is expected
    detector.detections = []
    observation, _ = vision.evaluate(content, reference, 12)
    assert observation.present is True and observation.held and not observation.current_confirmed
    observation, _ = vision.evaluate(content, reference, 22)
    assert observation.present is False
    detector.detections = [detected, cup(settings, image, shift=4)]
    observation, _ = vision.evaluate(content, reference, 26)
    assert observation.present is None
    assert "Mehrere" in observation.status


def test_dark_or_moved_machine_cannot_give_green(settings, image):
    detector = FakeDetector([cup(settings, image)])
    vision = Vision(detector, settings)
    reference = make_reference(image, settings)
    shifted = Image.new("RGB", image.size); shifted.paste(image, (16, 0))
    for timestamp in [1, 6, 11, 16]:
        observation, _ = vision.evaluate(jpeg(shifted), reference, timestamp)
        assert observation.present is None
    observation, _ = vision.evaluate(jpeg(Image.new("RGB", image.size)), reference, 21)
    assert observation.present is None


def test_person_covering_target_blocks_detection(settings, image):
    target = settings.zone.pixels(image.size)
    person = Detection((target[0]-100, target[1]-100, target[2]+100, target[3]+100), .95, 0)
    vision = Vision(FakeDetector([cup(settings, image), person]), settings)
    reference = make_reference(image, settings)
    for timestamp in (1, 6, 11):
        observation, _ = vision.evaluate(jpeg(image), reference, timestamp)
        assert observation.present is None
        assert observation.status == "Prüfbereich verdeckt"


def test_changed_zone_invalidates_reference(settings, image):
    reference = make_reference(image, settings)
    changed = replace(settings, zone=Rect(.476, .565, .535, .65))
    assert not check_alignment(image, changed, reference).ok
