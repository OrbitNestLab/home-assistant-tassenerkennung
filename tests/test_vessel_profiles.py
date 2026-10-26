import numpy as np
from PIL import Image, ImageDraw

from tassenerkennung.vessel_profiles import VesselProfiles
from tassenerkennung.vision import Settings


def setup():
    rng=np.random.default_rng(411)
    empty=np.clip(rng.normal(45,3,(56,48,3)),0,255).astype(np.uint8)
    cup=Image.fromarray(empty.copy())
    draw=ImageDraw.Draw(cup)
    draw.rounded_rectangle((12,8,36,51),radius=6,fill=(225,226,220))
    draw.ellipse((30,15,44,37),outline='white',width=3)
    profiles=VesselProfiles({'version':3,'camera':'camera.test','source_size':[1280,720],
        'zone_relative_box':[0,0,1,1],'names':{},
        'samples':{'leer':[empty.tolist()]*3,'kaffeetasse':[np.asarray(cup).tolist()]*3}})
    settings=Settings.from_dict({'camera':'camera.test'})
    return profiles,settings,empty,np.asarray(cup)


def frame(sample,settings):
    image=Image.new('RGB',(1280,720))
    left,top,right,bottom=settings.zone.pixels(image.size)
    patch=Image.fromarray(np.asarray(sample,dtype=np.uint8)).resize((right-left,bottom-top))
    image.paste(patch,(left,top))
    return image


def test_confirmed_placement_survives_a_soft_miss_and_rearms_after_empty():
    profiles,settings,empty,cup=setup()
    cup_frame=frame(cup,settings)
    for timestamp in (0,1,2,3):result=profiles.evaluate(cup_frame,settings,timestamp,True,has_vessel=True)
    assert result[0]=='kaffeetasse' and result[2]
    episode=result[2]
    classify=profiles.classify
    profiles.classify=lambda *args:('unbekannt',.5)
    assert profiles.evaluate(cup_frame,settings,4,True,has_vessel=True)[0]=='kaffeetasse'
    assert profiles.held and not profiles.current_confirmed
    profiles.classify=classify
    empty_frame=frame(empty,settings)
    for timestamp in (10,12,14,16):result=profiles.evaluate(empty_frame,settings,timestamp,True,has_vessel=False)
    assert result[0]=='leer' and result[2] is None
    for timestamp in (20,21,22,23):result=profiles.evaluate(cup_frame,settings,timestamp,True,has_vessel=True)
    assert result[0]=='kaffeetasse' and result[2] and result[2]!=episode


def test_lifecycle_can_be_restored_without_authorizing_a_current_frame():
    profiles,settings,_,cup=setup()
    for timestamp in (0,1,2,3):first=profiles.evaluate(frame(cup,settings),settings,timestamp,True,has_vessel=True)
    restored,_,_,_=setup()
    restored.restore_lifecycle(profiles.lifecycle)
    assert restored.episode==first[2]
    assert not restored.current_confirmed


def test_user_profile_export_and_camera_reset_keep_only_names():
    profiles,_,empty,cup=setup()
    profiles.add_samples('benutzer_gastbecher','Gastbecher',[cup.tolist()]*3)
    exported=profiles.export()
    assert VesselProfiles(exported).display_name('benutzer_gastbecher')=='Gastbecher'
    profiles.reset_for_camera('camera.new',[1280,720],[empty.tolist()]*3)
    assert set(profiles.samples)=={'leer'}
    assert profiles.display_name('benutzer_gastbecher')=='Gastbecher'
