"""Single-frame machine tracking, canonical zoom and strictly fresh recognition."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import time

import numpy as np
from PIL import Image, ImageDraw
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .camera_geometry import rectify
from .vision import Rect, Alignment, open_image, render

REASONS={
    'machine_not_visible':'Maschine wird im Kamerabild gesucht',
    'reference_features_missing':'Maschinenreferenz muss neu eingerichtet werden',
    'machine_motion_ambiguous':'Maschinenposition noch nicht eindeutig',
    'machine_partly_hidden':'Maschine ist teilweise verdeckt',
    'machine_outside_image':'Maschine ist nicht vollständig im Bild',
    'outlet_not_verified':'Auslauf ist noch nicht sicher erkannt',
    'no_space_below_outlet':'Unter dem Auslauf ist zu wenig Platz',
}


def active_settings(settings,model,tracking):
    w,h=model['source_size'];l,t,r,b=model['body'];bw,bh=r-l,b-t
    crop=Rect(max(0,l-bw*.09)/w,max(0,t-bh*.06)/h,min(w,r+bw*.09)/w,min(h,b+bh*.08)/h)
    zone=Rect(*(v/d for v,d in zip(tracking['canonical_zone'],(w,h,w,h))))
    return replace(settings,crop=crop,zone=zone)


def overview(content,tracking,message):
    image=open_image(content);image.thumbnail((960,600));draw=ImageDraw.Draw(image)
    source=open_image(content).size;sx,sy=image.width/source[0],image.height/source[1]
    for key,color in [('body','#70b8ec'),('outlet','#ffc36b'),('zone','#62d4b0')]:
        points=tracking.get(key)
        if points:
            coords=[(round(x*sx),round(y*sy)) for x,y in points]
            draw.line(coords+[coords[0]],fill=color,width=2)
    if abs(tracking.get('angle',0))>135:
        image=image.rotate(180);draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,image.width,28),fill='#172635')
    draw.text((9,8),message[:100],fill='#eef5fa')
    out=BytesIO();image.save(out,format='JPEG',quality=85);return out.getvalue()


async def update(c):
    started=time.monotonic()
    content=await c._capture();captured_at=dt_util.utcnow();stamp=time.monotonic()
    c.raw_content=content
    digest=sha256(content).hexdigest()
    if digest==c.frame_hash:
        if c.frame_changed_mono is None or stamp-c.frame_changed_mono>c.ttl:
            raise ValueError('camera_image_not_updating')
        return c.data
    c.frame_hash,c.frame_changed_mono=digest,stamp
    if not c.reference or not c.reference.get('camera_context'):
        c.tracking_info={'ok':False,'confirmed':False,'reason':'reference_features_missing'}
        c.tracking_count=0
        return await _unavailable(c,content,captured_at,stamp,'Maschinenreferenz bitte einmal einrichten')
    response=await c.hass.async_add_executor_job(c.engine.detector.track_machine,content,c.reference,c.settings,c.tracking_previous)
    tracking=response['tracking'];model=response['model']
    if not c.reference.get('machine_model'):
        c.reference={**c.reference,'machine_model':model};await c.store.async_save(c.reference)
    if not tracking.get('ok'):
        c.tracking_info={**tracking,'confirmed':False};c.tracking_count=0;c.tracking_previous=None
        if c.failure_since is None:c.failure_since=stamp
        if stamp-c.failure_since>=12 and c.recovery_state not in ['fehlgeschlagen']:
            await c._tracking_status('fehlgeschlagen')
        return await _unavailable(c,content,captured_at,stamp,REASONS.get(tracking.get('reason'),'Maschine wird gesucht'))
    body=np.asarray(tracking['body'])
    motion=float(np.max(np.linalg.norm(body-np.asarray(c.tracking_last_body),axis=1))) if c.tracking_last_body is not None else 0
    c.tracking_count=min(6,c.tracking_count+1) if motion<=5 else 1
    c.tracking_last_body=body.tolist();c.tracking_previous=tracking['matrix']
    confirmed=c.tracking_count>=3
    c.tracking_info={**tracking,'confirmed':confirmed,'frames':c.tracking_count,'method':'Maschinenmerkmale + unabhängiger Auslaufvergleich'}
    if c.failure_since is not None and confirmed:await c._tracking_status('erfolgreich')
    c.failure_since=None
    reference={**c.reference,'camera_transform':tracking['matrix']}
    canonical=await c.hass.async_add_executor_job(rectify,content,reference)
    active=active_settings(c.settings,model,tracking);c.active_settings=active;c.latest_content=canonical
    lit=all(c.hass.states.is_state(e,'on') for e in c.lights)
    alignment=Alignment(confirmed,'Maschine und Auslauf bestätigt' if confirmed else 'Maschinenposition wird bestätigt',min(tracking['body_score'],tracking['outlet_score']))
    observed_empty=False
    if confirmed:
        evaluation_reference={**reference,'machine_model':{**model,'outlet':tracking['canonical_outlet']}}
        observation,jpeg=await c.hass.async_add_executor_job(c.engine.evaluate,canonical,evaluation_reference,stamp,alignment,active,c.learning.active)
        status=observation.status
        observed_empty=observation.absent and observation.vessel_type=='leer' and observation.vessel_current_confirmed
        pour_x=sum(tracking['canonical_outlet'][::2])/2
        hints=[d for d in observation.detections if d.class_id in {39,40,41} and d.in_zone(active.zone,observation.image_size)
               and d.box[0]<pour_x<d.box[2] and d.score>=.60]
        c.tracking_info['becher_hinweis']=len(hints)==1
        if hints and not c.candidate_hint and not lit and not c.learning.active:
            c.hass.bus.async_fire('tassenerkennung_becher_hinweis',{'kamera':c.settings.camera,'bildzeitpunkt':captured_at.isoformat()})
        c.candidate_hint=bool(hints)
        if not lit:
            # Ambient observations may establish an empty tray or request light,
            # but never become a current beverage authorization.
            observation.present=None;observation.current_confirmed=False;observation.vessel_current_confirmed=False
            observation.held=False;observation.vessel_held=False
            c.engine.confirmation.reset();c.engine.last_positive=None
            status='Becher sichtbar · beleuchtete Bestätigung fehlt' if hints else 'Maschine erkannt · Licht für Becherprüfung einschalten'
            observation.status=status
            jpeg=await c.hass.async_add_executor_job(lambda:render(open_image(canonical),active,observation))
    else:
        observation=None;c.engine.confirmation.reset();c.engine.last_positive=None;c.engine.profiles.invalidate()
        status='Maschine erkannt · Licht für Becherprüfung einschalten' if confirmed else 'Maschinenposition wird bestätigt'
        jpeg=await c.hass.async_add_executor_job(lambda:render(open_image(canonical),active,message=status))
    total=(time.monotonic()-started)*1000
    if time.monotonic()-stamp>c.ttl:raise ValueError('processing_too_slow')
    c.last_capture,c.last_capture_mono=captured_at,stamp;c._cancel_expiry()
    c.expiry_cancel=async_call_later(c.hass,max(.1,c.ttl-(time.monotonic()-stamp)),lambda _:c._invalidate('Kamerabild veraltet'))
    if observation and not c.learning.active:
        profiles=c.engine.profiles
        if profiles.lifecycle!=c.saved_lifecycle:
            await c.vessel_store.async_save(profiles.lifecycle);c.saved_lifecycle=profiles.lifecycle
        empty=observed_empty
        if empty:
            if c.absent_since is None:
                since=c.engine.profiles.since
                c.absent_since=captured_at-timedelta(seconds=max(0,stamp-since)) if since is not None else captured_at
            if (captured_at-c.absent_since).total_seconds()>=5:c.empty_confirmed=True
        else:c.absent_since=None
        if not observation.alignment.ok:c.empty_confirmed=False
        if observation.current_confirmed and c.empty_confirmed:c.placed_at=captured_at;c.empty_confirmed=False
    else:
        # Keep an already verified empty tray across a temporary dark frame.
        # Fresh vessel, type, light and machine checks are still mandatory.
        c.absent_since=None
    return {'observation':observation,'status':status,'jpeg':jpeg,'raw_jpeg':content,
            'tracking_jpeg':await c.hass.async_add_executor_job(overview,content,tracking,status),
            'captured_at':captured_at,'fresh':True,'error':None,'capture_ms':round((stamp-started)*1000,1),
            'total_ms':round(total,1)}


async def _unavailable(c,content,captured_at,stamp,message):
    c.engine.confirmation.reset();c.engine.last_positive=None;c.engine.profiles.invalidate()
    # Image loss never creates an empty state, but it also need not discard a
    # genuinely verified empty tray from immediately before the dark interval.
    c.absent_since=None
    c.candidate_hint=False
    c.last_capture,c.last_capture_mono=captured_at,stamp
    image=await c.hass.async_add_executor_job(overview,content,{},message)
    return {'observation':None,'status':message,'jpeg':image,'tracking_jpeg':image,'raw_jpeg':content,
            'captured_at':captured_at,'fresh':True,'error':None}
