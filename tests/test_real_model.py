"""Run the shipped ONNX model, including its real input-size contract."""
from pathlib import Path
from dataclasses import replace
import pytest
from PIL import Image
from tassenerkennung.vision import Detector,Settings,Rect

ROOT=Path(__file__).parents[1]

@pytest.fixture(scope='module')
def model():
    path=ROOT/'addon/modelle/yolox_s.onnx'
    if not path.exists():pytest.skip('Model not installed')
    return Detector(path)

def test_shipped_model_runs_at_its_own_declared_input_size(model):
    settings=replace(Settings.from_dict({}),crop=Rect(0,0,1,1))
    assert model.input_width==model.session.get_inputs()[0].shape[-1]
    assert model.input_height==model.session.get_inputs()[0].shape[-2]
    assert model.detect(Image.new('RGB',(480,360),'black'),settings)==[]

def test_current_real_empty_tray_is_not_a_vessel(model):
    path=ROOT/'Ueberarbeitung-v070/privat/empty-canonical-1.jpg'
    if not path.exists():pytest.skip('Private camera regression is not distributed')
    import json
    data=json.loads((path.parent/'new-reference.json').read_text());m=data['machine_model'];w,h=m['source_size']
    l,t,r,b=m['body'];zone=Rect(*(v/d for v,d in zip(m['zone'],(w,h,w,h))))
    settings=replace(Settings.from_dict({}),confidence=.4,crop=Rect((l-15)/w,(t-12)/h,(r+15)/w,(b+12)/h),zone=zone)
    image=Image.open(path).convert('RGB')
    assert not [d for d in model.detect(image,settings) if d.class_id in {39,40,41} and d.in_zone(zone,image.size)]
