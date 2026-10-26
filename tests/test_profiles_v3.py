import numpy as np
from PIL import Image,ImageDraw
from tassenerkennung.vessel_profiles import VesselProfiles
from tassenerkennung.vision import Settings

def data():
    rng=np.random.default_rng(887)
    empty=np.clip(rng.normal(45,3,(56,48,3)),0,255).astype(np.uint8)
    cup=Image.fromarray(empty.copy());d=ImageDraw.Draw(cup);d.rounded_rectangle((13,9,36,50),radius=6,fill=(230,231,225));d.ellipse((30,15,44,36),outline='white',width=3)
    other=Image.fromarray(empty.copy());ImageDraw.Draw(other).rectangle((6,5,40,51),fill=(40,150,80))
    p=VesselProfiles({'version':3,'camera':'camera.eve_cam_07b0','source_size':[1280,720],
      'zone_relative_box':[0,0,1,1],'samples':{'leer':[empty.tolist()]*3,'kaffeetasse':[np.asarray(cup).tolist()]*3}})
    s=Settings.from_dict({});return p,s,empty,cup,other

def frame(sample,s):
    image=Image.new('RGB',(1280,720));l,t,r,b=s.zone.pixels(image.size)
    image.paste(Image.fromarray(np.asarray(sample,dtype=np.uint8)).resize((r-l,b-t)),(l,t));return image

def test_foreground_distinguishes_empty_known_and_unknown():
    p,s,empty,cup,other=data()
    assert p.classify(frame(empty,s),s)[0]=='leer'
    assert p.classify(frame(cup,s),s)[0]=='kaffeetasse'
    assert p.classify(frame(other,s),s)[0]=='unbekannt'

def test_empty_only_profile_is_valid_and_never_confirms_a_cup():
    p,s,empty,cup,_=data();p.samples={'leer':p.samples['leer']}
    assert p.classify(frame(empty,s),s)[0]=='leer'
    assert p.classify(frame(cup,s),s)[0]=='unbekannt'

def test_profile_cannot_authorize_when_object_detector_disagrees():
    p,s,empty,cup,_=data()
    for at in range(8):p.evaluate(frame(cup,s),s,at,True,has_vessel=False)
    assert not p.current_confirmed

def test_equal_looking_profiles_are_rejected_as_ambiguous():
    p,s,_,cup,_=data();p.samples['babybecher']=[a.copy() for a in p.samples['kaffeetasse']]
    assert p.classify(frame(cup,s),s)[0]=='unbekannt'
    assert 'ähnlich' in p.rejection_reason

def test_stable_real_cup_score_just_above_release_threshold_is_accepted():
    p,s,_,cup,_=data();original=p.appearance_score
    p.appearance_score=lambda *args:.761
    try:label,score=p.classify(frame(cup,s),s)
    finally:p.appearance_score=original
    assert label=='kaffeetasse' and score==.761
