from dataclasses import replace
import numpy as np
from PIL import Image
from tassenerkennung.vessel_profiles import VesselProfiles
from fixtures import profiles, scene, settings


def setup():
 return profiles(),settings(),scene('babybecher')

def test_current_five_seconds_and_episode_persists_over_uncertainty():
 p,s,im=setup()
 for t in range(2):assert p.evaluate(im,s,t,True)[0]=='unbekannt'
 label,score,episode=p.evaluate(im,s,2,True);assert label=='babybecher' and episode
 assert p.evaluate(im,s,6,False)[0]=='unbekannt'
 for t in range(7,13):last=p.evaluate(im,s,t,True)
 assert last[0]=='babybecher' and last[2]==episode
 # Explicit sustained empty state rearms a subsequent real placement.
 empty=scene('leer')
 for t in range(13,19):last=p.evaluate(empty,s,t,True)
 assert last[0]=='leer' and last[2] is None
 for t in range(19,25):last=p.evaluate(im,s,t,True)
 assert last[0]=='babybecher' and last[2]!=episode

def test_grayscale_dark_flat_or_wrong_camera_do_not_match_baby():
 p,s,im=setup()
 assert p.classify(im.convert('L').convert('RGB'),s)[0]!='babybecher'
 assert p.classify(Image.new('RGB',im.size),s)[0]=='unbekannt'
 assert p.classify(Image.new('RGB',im.size,'cyan'),s)[0]=='unbekannt'
 assert p.classify(im,replace(s,camera='camera.other'))[0]=='unbekannt'
 assert p.classify(im.resize((640,360)),s)[0]=='unbekannt'

def test_moderate_brightness_and_position_change():
 p,s,im=setup()
 darker=Image.fromarray((np.asarray(im)*.8).astype('uint8'))
 assert p.classify(darker,s)[0]=='babybecher'
 moved=Image.new('RGB',im.size);moved.paste(im,(3,0))
 assert p.classify(moved,s)[0]=='babybecher'

def test_hold_is_display_only_and_one_soft_miss_keeps_progress():
 p,s,im=setup()
 for at in [0,1,2]:result=p.evaluate(im,s,at,True)
 assert result[0]=='babybecher' and p.current_confirmed
 classify=p.classify;p.classify=lambda *args:('unbekannt',.5)
 result=p.evaluate(im,s,3,True)
 assert result[0]=='babybecher' and p.held and not p.current_confirmed
 p.classify=classify
 result=p.evaluate(im,s,4,True)
 assert result[0]=='babybecher' and p.current_confirmed and not p.held
 assert p.evaluate(im,s,5,False)[0]=='unbekannt'
 assert not p.current_confirmed and not p.held

def test_restart_keeps_the_same_confirmed_placement():
 p,s,im=setup()
 for at in [0,1,2]:first=p.evaluate(im,s,at,True)
 state=p.lifecycle
 restored=profiles()
 restored.restore_lifecycle(state)
 for at in [10,11,12]:second=restored.evaluate(im,s,at,True)
 assert first[0]==second[0]=='babybecher' and first[2]==second[2]


def test_user_profile_can_be_added_exported_and_reloaded():
 p,s,im=setup()
 sample=p.extract_sample(im,s)
 p.add_samples('benutzer_gastbecher','Gastbecher',[sample]*3)
 exported=p.export()
 restored=VesselProfiles(exported)
 assert restored.display_name('benutzer_gastbecher')=='Gastbecher'
 assert len(restored.samples['benutzer_gastbecher'])==3
 assert exported['version']==2


def test_camera_change_resets_old_images_but_keeps_profile_names():
 p,s,im=setup();p.names['benutzer_gast']='Gastbecher'
 empty=p.extract_sample(Image.new('RGB',im.size,'gray'),s)
 p.reset_for_camera('camera.new',[1280,720],[empty]*3)
 assert set(p.samples)=={'leer'} and p.data['camera']=='camera.new'
 assert p.names['benutzer_gast']=='Gastbecher'
