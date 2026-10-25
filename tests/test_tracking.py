from dataclasses import replace
from io import BytesIO
import numpy as np
from PIL import Image
from tassenerkennung.vision import Settings,Vision,Detection,make_reference,Confirmation
from tassenerkennung.tracking import add_landmarks,locate,shifted_settings

def fixture():
 s=Settings.from_dict({})
 image=Image.fromarray(np.random.default_rng(112).integers(0,255,(720,1280,3),dtype=np.uint8))
 ref=add_landmarks(make_reference(image,s),image,s)
 return s,image,ref

def test_dual_reference_relocates_bounded_translation():
 s,image,ref=fixture()
 moved=Image.new('RGB',image.size);moved.paste(image,(32,16))
 assert not locate(moved,s,ref).ok
 found=locate(moved,s,ref,recovery=True)
 assert found.ok and found.shift==(16,8)
 assert locate(moved,s,{**ref,'offset':list(found.shift)}).ok
 assert shifted_settings(s,ref,found.shift).zone.left>s.zone.left
 assert ref['offset']==[0,0]

def test_outlet_and_body_must_agree():
 s,image,ref=fixture();moved=Image.new('RGB',image.size);moved.paste(image,(32,0))
 # Restore only one landmark; inconsistent shifts must not be accepted.
 l,t,r,b=ref['landmarks'][1]['rect'];box=(l*2,t*2,r*2,b*2)
 moved.paste(image.crop(box),box)
 assert not locate(moved,s,ref,recovery=True).ok

def test_large_shift_or_missing_second_anchor_blocks():
 s,image,ref=fixture();moved=Image.new('RGB',image.size);moved.paste(image,(200,0))
 assert not locate(moved,s,ref,recovery=True).ok
 assert not locate(image,s,{**ref,'landmarks':[]}).ok

def test_minimum_five_seconds_not_only_frame_count():
 confirm=Confirmation(5,3,5);d=Detection((0,0,40,40),.9)
 for at in range(5):assert not confirm.update(d,at)
 assert confirm.update(d,5)
 assert not confirm.update(d,10)

def test_held_display_never_keeps_brew_permission():
 s,image,ref=fixture();box=s.zone.pixels(image.size)
 class Fake:
  detections=[Detection((box[0]+5,box[1]+3,box[2]-5,box[3]-3),.9)]
  def detect(self,*args):return self.detections
 f=Fake();engine=Vision(f,s);out=BytesIO();image.save(out,format='PNG');content=out.getvalue()
 for t in range(6):obs,_=engine.evaluate(content,ref,t)
 assert obs.current_confirmed and obs.present
 f.detections=[]
 obs,_=engine.evaluate(content,ref,6)
 assert obs.held and obs.present and not obs.current_confirmed and obs.absent
 obs,_=engine.evaluate(content,ref,16)
 assert not obs.present and not obs.held
 # Lost alignment immediately clears even the display's grace period.
 f.detections=[Detection((box[0]+5,box[1]+3,box[2]-5,box[3]-3),.9)]
 for t in range(17,23):obs,_=engine.evaluate(content,ref,t)
 assert obs.present
 obs,_=engine.evaluate(content,None,23)
 assert obs.present is None and not obs.current_confirmed and not obs.held

def test_weak_cup_does_not_rearm_empty_area():
 s,image,ref=fixture();l,t,r,b=s.zone.pixels(image.size)
 class Fake:
  def detect(self,*a):return [Detection((l+5,t+3,r-5,b-3),.5)]
 engine=Vision(Fake(),s);out=BytesIO();image.save(out,format='PNG')
 for at in range(12):obs,_=engine.evaluate(out.getvalue(),ref,at)
 assert not obs.current_confirmed and not obs.absent
