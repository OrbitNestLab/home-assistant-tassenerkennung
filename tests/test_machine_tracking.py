"""Actual private reference regression; synthetic motions keep geometry measurable."""
from pathlib import Path
from io import BytesIO
import base64
import json
import sys
import cv2
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'addon'))
from visionlib.machine import bootstrap,track,setup

ROOT=Path(__file__).parents[1]/'Ueberarbeitung-v070'/'privat'

@pytest.fixture
def machine():
    path=ROOT/'new-reference-draft.json'
    if not path.exists():pytest.skip('Private camera regression data is not distributed')
    reference=json.loads(path.read_text());model=reference['machine_model']
    raw=base64.b64decode(reference['camera_context']['png'])
    image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
    return reference,model,image

def encoded(image):
    ok,buf=cv2.imencode('.png',image);assert ok
    return base64.b64encode(buf).decode()

@pytest.mark.parametrize('angle,scale,dx,dy',[(0,1,0,0),(180,1,0,0),(0,.8,40,-10),(8,1.1,-35,20),(-9,.9,-45,-20)])
def test_body_and_outlet_follow_known_camera_motion(machine,angle,scale,dx,dy):
    ref,model,image=machine;h,w=image.shape[:2]
    A=cv2.getRotationMatrix2D((w/2,h/2),angle,scale);A[:,2]+=[dx,dy]
    moved=cv2.warpAffine(image,A,(w,h))
    result=track(ref,encoded(moved),model)
    assert result['ok'],result
    H=np.vstack((A,[0,0,1]));l,t,r,b=model['body']
    expected=cv2.perspectiveTransform(np.float32([[l,t],[r,t],[r,b],[l,b]]).reshape(-1,1,2),H).reshape(-1,2)
    assert np.max(np.linalg.norm(np.asarray(result['body'])-expected,axis=1))<3
    assert result['outlet_score']>=.70

@pytest.mark.parametrize('kind',['missing','covered_outlet','mirrored','dark'])
def test_tracking_does_not_authorize_missing_or_hidden_machine(machine,kind):
    ref,model,image=machine;image=image.copy();l,t,r,b=model['body']
    if kind=='missing':image[t:b,l:r]=220
    elif kind=='covered_outlet':
        l,t,r,b=model['outlet'];image[t-8:b+8,l-8:r+8]=0
    elif kind=='mirrored':image=cv2.flip(image,1)
    else:image[:]=0
    assert not track(ref,encoded(image),model)['ok']
    assert not track(ref,encoded(image),model,previous=np.eye(3).tolist())['ok']

def test_switched_off_display_keeps_body_and_outlet_tracking(machine):
    ref,model,_=machine;path=ROOT/'current-check.jpg'
    if not path.exists():pytest.skip('Private powered-off camera regression not distributed')
    H=[[-1,0,1279],[0,-1,719],[0,0,1]]
    result=track(ref,base64.b64encode(path.read_bytes()).decode(),model,previous=H)
    assert result['ok'],result
    assert result['outlet_score']>=.7 and result['body_score']>=.5


def test_manual_body_setup_still_finds_outlet_automatically(machine):
    ref,model,image=machine;w,h=model['source_size']
    result=setup(ref,[v/d for v,d in zip(model['body'],(w,h,w,h))])
    assert result['outlet'] and result['body']==model['body']
    assert abs(sum(result['outlet'][::2])/2-sum(model['outlet'][::2])/2)<10
    with pytest.raises(ValueError):setup(ref,[0,0,.01,.01])


def test_visible_front_and_outlet_allow_occluded_side_but_not_hidden_outlet():
    reference_path=ROOT/'new-reference.json';image_path=ROOT/'online-source.jpg'
    if not reference_path.exists() or not image_path.exists():
        pytest.skip('Private partial-occlusion camera regression not distributed')
    ref=json.loads(reference_path.read_text());model=ref['machine_model']
    raw=image_path.read_bytes();result=track(ref,base64.b64encode(raw).decode(),model)
    assert result['ok'],result
    assert result['partly_hidden']
    assert result['bezel_score']>=.60 and result['outlet_score']>=.78
    image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
    for part in ['outlet','body']:
        hidden=image.copy()
        if part=='outlet':
            points=np.asarray(result['outlet'],np.int32)
            x,y,w,h=cv2.boundingRect(points)
            hidden[max(0,y-12):y+h+12,max(0,x-12):x+w+12]=0
        else:
            cv2.fillConvexPoly(hidden,np.asarray(result['body'],np.int32),(220,220,220))
        assert not track(ref,encoded(hidden),model,previous=result['matrix'])['ok'],part
