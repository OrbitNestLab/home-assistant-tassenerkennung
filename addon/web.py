"""Ingress management UI, bounded HA allowlist and observable background actions."""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

APP_VERSION='0.4.1'
ENTITIES={
 'status':'sensor.tassenerkennung_status','type':'sensor.tassenerkennung_bechertyp',
 'cup':'binary_sensor.tassenerkennung_tasse_unter_dem_auslauf',
 'learning':'binary_sensor.tassenerkennung_einlernmodus','learning_status':'sensor.tassenerkennung_einlernstatus',
 'profile':'select.tassenerkennung_einlernprofil','name':'text.tassenerkennung_name_fur_neuen_becher',
 'source_camera':'select.tassenerkennung_quellkamera_fur_wechsel'}
BUTTONS={'start':'button.tassenerkennung_einlernen_starten','capture':'button.tassenerkennung_aufnahme_bestatigen',
 'save':'button.tassenerkennung_profil_speichern','cancel':'button.tassenerkennung_einlernen_abbrechen',
 'refresh':'button.tassenerkennung_jetzt_prufen','apply_camera':'button.tassenerkennung_ausgewahlte_kamera_ubernehmen',
 'align':'button.tassenerkennung_ausrichtung_bestatigen'}
SAFE_ATTRIBUTES={'version','bild_aktuell','bildzeitpunkt','referenz_vorhanden','kamera_korrigiert','nachfuehrung',
 'aktuell_bestaetigt','anzeige_gehalten','schritt','status','profil','aufnahmen','benoetigte_aufnahmen',
 'getraenkeaktionen_gesperrt','options','friendly_name','maschine','profile','kamera','aufnahmelauf',
 'teilaufnahmen','fehler','vergleichswerte','ablehnungsgrund','gesamtzeit_ms','aufnahmezeit_ms','pruefabstand_sekunden','becher_hinweis'}

class HomeAssistantClient:
 def __init__(self,token=None,base='http://supervisor/core/api'):
  self.token=token or os.environ.get('SUPERVISOR_TOKEN','');self.base=base.rstrip('/')
  self.cache=None;self.cache_time=0.;self.cache_lock=threading.Lock()
  if len(self.token)<20:raise ValueError('home_assistant_api_unavailable')
 def _request(self,path,payload=None,accept='application/json'):
  headers={'Authorization':'Bearer '+self.token,'Accept':accept};data=None
  if payload is not None:data=json.dumps(payload).encode();headers['Content-Type']='application/json'
  req=urllib.request.Request(self.base+path,data=data,headers=headers)
  with urllib.request.urlopen(req,timeout=45 if payload is not None else 10) as response:
   content=response.read(4_000_001)
   if len(content)>4_000_000:raise ValueError('response_too_large')
   return content,response.headers.get_content_type()
 def state(self,entity):
  content,_=self._request('/states/'+entity);value=json.loads(content)
  return {'state':value.get('state','unknown'),'attributes':{k:v for k,v in value.get('attributes',{}).items() if k in SAFE_ATTRIBUTES}}
 def status(self):
  with self.cache_lock:
   if self.cache is not None and time.monotonic()-self.cache_time<1.:return self.cache
  result={k:{'state':'unavailable','attributes':{}} for k in ENTITIES};connected=False
  try:
   content,_=self._request('/states');states={s.get('entity_id'):s for s in json.loads(content)};connected=True
   for key,entity in ENTITIES.items():
    value=states.get(entity,{})
    result[key]={'state':value.get('state','unavailable'),'attributes':{k:v for k,v in value.get('attributes',{}).items() if k in SAFE_ATTRIBUTES}}
  except (urllib.error.HTTPError,urllib.error.URLError,ValueError,TimeoutError):pass
  data={'app_version':APP_VERSION,'entities':result,'connected':connected,
        'ready':result['status']['attributes'].get('version') == '0.7.0'}
  with self.cache_lock:self.cache=data;self.cache_time=time.monotonic()
  return data
 def camera(self,source=False,tracking=False,raw=False):
  entity=self.status()['entities']['status']['attributes'].get('kamera','') if raw else 'camera.tassenerkennung_tracking' if tracking else 'camera.tassenerkennung_kameravorschau' if source else 'camera.tassenerkennung_prufbild'
  if not re.fullmatch(r'camera\.[a-z0-9_]+',entity):raise ValueError('Aktive Kamera fehlt')
  return self._request('/camera_proxy/'+entity,accept='image/jpeg')
 def _service(self,domain,service,entity,data=None):
  payload={'entity_id':entity} if entity else {}
  if data:payload.update(data)
  try:self._request('/services/'+domain+'/'+service,payload)
  except urllib.error.HTTPError as error:
   try:message=json.loads(error.read(1500)).get('message','')
   except Exception:message=''
   raise ValueError(str(message)[:250] or 'Home Assistant konnte diese Aktion nicht ausführen.') from error
  finally:self.cache_time=0.
 def action(self,payload):
  action=str(payload.get('action',''))
  if action=='calibrate':
   box=payload.get('body');rotation=payload.get('rotation',0)
   if not isinstance(box,list) or len(box)!=4 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in box):raise ValueError('Bitte die Maschine mit einem Rahmen markieren.')
   if not (0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1) or rotation not in [0,180]:raise ValueError('Der markierte Rahmen ist ungültig.')
   camera=self.state(ENTITIES['status'])['attributes'].get('kamera','')
   if not re.fullmatch(r'camera\.[a-z0-9_]+',camera):raise ValueError('Die aktive Kamera fehlt.')
   self._service('tassenerkennung','calibrate_machine',None,{'camera':camera,'body':box,'rotation':rotation})
   return {'ok':True,'action':action}
  if action in BUTTONS:
   if action=='start' and payload.get('name') is not None and self.state(ENTITIES['profile'])['state']=='+ Neuer Becher':
    self.action({'action':'set_name','value':payload['name']})
   self._service('button','press',BUTTONS[action]);return {'ok':True,'action':action}
  if action in ['select_profile','select_camera']:
   key='profile' if action=='select_profile' else 'source_camera';option=str(payload.get('option',''))[:100]
   if option not in self.state(ENTITIES[key])['attributes'].get('options',[]):raise ValueError('Bitte eine verfügbare Auswahl verwenden.')
   self._service('select','select_option',ENTITIES[key],{'option':option});return {'ok':True,'action':action}
  if action=='set_name':
   value=str(payload.get('value','')).strip()[:60]
   if not re.fullmatch(r'[^<>]{0,60}',value):raise ValueError('Bitte einen gültigen Bechernamen eingeben.')
   self._service('text','set_value',ENTITIES['name'],{'value':value});return {'ok':True,'action':action}
  raise ValueError('Diese Aktion ist nicht erlaubt.')

class ActionJobs:
 """One management action at a time, with a real result instead of HTTP timeouts."""
 def __init__(self,client):
  self.client=client;self.lock=threading.Lock();self.job=None;self.events=deque(maxlen=16)
 def snapshot(self):
  with self.lock:return {'job':dict(self.job) if self.job else None,'events':list(self.events)}
 def submit(self,payload):
  action=payload.get('action')
  if action not in {*BUTTONS,'select_profile','select_camera','set_name','calibrate'}:raise ValueError('Diese Aktion ist nicht erlaubt.')
  with self.lock:
   if self.job and self.job['state']=='running':raise RuntimeError('Eine Aktion läuft bereits. Bitte kurz warten.')
   self.job={'id':uuid.uuid4().hex,'action':action,'state':'running','started':datetime.now(timezone.utc).isoformat(),'message':''}
   job=dict(self.job)
  threading.Thread(target=self._run,args=(payload,job['id']),daemon=True).start()
  return {'ok':True,'job':job}
 def _run(self,payload,job_id):
  error=None
  try:self.client.action(payload)
  except ValueError as e:error=str(e)[:250]
  except Exception:error='Die Aktion konnte nicht abgeschlossen werden. Prüfe Kamera, Licht und Verbindung in den Details.'
  with self.lock:
   self.job.update(state='error' if error else 'done',message=error or 'Abgeschlossen',finished=datetime.now(timezone.utc).isoformat())
   self.events.appendleft(dict(self.job))

HTML=r'''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Tassenerkennung</title>
<style>
:root{color-scheme:dark;--bg:#0e1821;--card:#192733;--line:#314553;--text:#edf3f6;--muted:#a6b8c4;--good:#66d7b4;--amber:#ffc178;--red:#ff938e}*{box-sizing:border-box}body{margin:0;background:radial-gradient(at 90% 0,#153c47,transparent 55%),var(--bg);color:var(--text);font:15px system-ui,-apple-system,sans-serif}main{max-width:1280px;margin:auto;padding:24px}header{display:flex;gap:14px;align-items:center;margin-bottom:22px}.logo{background:#254237;border-radius:18px;padding:12px;font-size:28px}h1{font-size:26px;margin:0}p{line-height:1.5}.muted,small{color:var(--muted)}header p{margin:4px 0 0}.version{margin-left:auto;color:var(--muted);font-size:12px;text-align:right}.layout{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(340px,.8fr);gap:18px}.card{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:20px;min-width:0}h2{font-size:19px;margin:0 0 16px}h3{font-size:16px;margin:0 0 8px}.tabs,.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.tabs{margin-bottom:12px}.tabs button{padding:9px 12px;font-size:13px;background:transparent;border:1px solid var(--line)}.tabs button.selected{background:#2a4b49;border-color:var(--good)}button{font:600 14px system-ui;padding:12px 15px;border:1px solid transparent;border-radius:12px;background:#2b4252;color:var(--text);cursor:pointer}button.primary{background:var(--good);color:#082a20}button.secondary{background:transparent;border-color:var(--line)}button:disabled{opacity:.4;cursor:default}button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid #6cc4ff;outline-offset:3px}.live{position:relative;border:1px solid var(--line);border-radius:15px;overflow:hidden;background:#0d1720;aspect-ratio:4/3;display:grid;place-items:center}.live img{width:100%;height:100%;object-fit:contain;position:absolute}.live .badge{position:absolute;right:10px;top:10px;padding:5px 9px;background:#101c27dd;border-radius:20px;font-size:12px}.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:13px}.fact{padding:11px;background:#12202a;border-radius:12px}.fact span{font-size:12px;color:var(--muted)}.fact b{display:block;margin-top:4px;font-size:14px}.good{color:var(--good)}.warn{color:var(--amber)}.error{color:var(--red)}.status{margin:14px 0 4px;font-weight:600;font-size:17px}.caption{min-height:38px;margin:5px 0 14px;color:var(--muted);font-size:13px}.notice{border-radius:12px;background:#163630;color:#bae4d3;padding:12px;margin-top:14px;font-size:13px}.field{display:block;font-size:13px;color:var(--muted);margin:12px 0 6px}select,input{width:100%;background:#122330;border:1px solid var(--line);border-radius:11px;padding:12px;color:var(--text);font:15px system-ui}.steps{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin:19px 0}.step{height:5px;border-radius:8px;background:#344955}.step.done,.step.active{background:var(--good)}.instruction{font-size:21px;line-height:1.35;margin:12px 0 8px;min-height:55px}.help{color:var(--muted);font-size:14px;min-height:42px}.actions{display:flex;gap:9px;margin-top:18px}.actions .primary{flex:1}.message{min-height:24px;margin:12px 0;font-size:14px;line-height:1.5}.progress{height:5px;background:#2a3e4d;border-radius:8px;overflow:hidden;margin:10px 0}.progress i{display:block;background:var(--good);height:100%;width:0;transition:width .25s}.profiles{display:grid;grid-template-columns:1fr 1fr;gap:9px}.profile{text-align:left;background:#12232e;border:1px solid var(--line);padding:11px;border-radius:12px}.profile b{display:block;font-size:14px}.profile small{display:block;margin-top:5px;font-size:12px}.section{margin-top:18px}details{border:1px solid var(--line);border-radius:16px;padding:15px;background:#14232e;margin-top:16px}summary{cursor:pointer;font-weight:600}details p{font-size:13px;color:var(--muted)}.preview{width:100%;border-radius:12px;margin-top:10px;max-height:240px;object-fit:contain}.diag{font-size:13px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}.events{list-style:none;padding:0;font-size:12px;color:var(--muted)}.events li{margin:9px 0;padding-bottom:9px;border-bottom:1px solid #2a3c48}.hidden,[hidden]{display:none!important}footer{font-size:12px;color:var(--muted);margin:20px 0} .spinner{display:inline-block;width:12px;height:12px;border:2px solid #5c7f77;border-top-color:var(--good);border-radius:50%;animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}@media(max-width:850px){main{padding:14px}.layout{grid-template-columns:1fr}.card{padding:16px}h1{font-size:23px}.version{font-size:10px}.live{aspect-ratio:4/3}.instruction{min-height:0}.help{min-height:0}}@media(max-width:420px){.facts{grid-template-columns:1fr}.fact{display:flex;justify-content:space-between;align-items:center}.fact b{margin:0}.profiles{grid-template-columns:1fr}}
canvas{width:100%;height:auto;border-radius:12px;background:#0d1720;touch-action:none;cursor:crosshair;margin-top:12px}</style></head><body><main>
<header><div class="logo" aria-hidden="true">☕</div><div><h1>Tassenerkennung</h1><p class="muted">Maschine im Blick. Becher sicher zuordnen.</p></div><div class="version" id="version">Verbindung wird geprüft</div></header>
<div class="layout"><div><section class="card"><h2>Live-Prüfung</h2><div class="tabs" role="group" aria-label="Kameraansicht"><button class="selected" data-view="zoom">Auslauf nah</button><button data-view="tracking">Maschine verfolgen</button><button data-view="raw">Kamera komplett</button></div><div class="live"><img id="camera" alt="Aktuelle Kameraansicht der Kaffeemaschine"><span class="badge" id="imageAge">Bild wird geladen</span></div><div class="status" id="mainStatus">Verbindung wird geprüft …</div><p class="caption" id="caption">Kamera, Maschinenposition und Becher werden getrennt geprüft.</p><div class="facts"><div class="fact"><span>Maschine</span><b id="machine">Wird geprüft</b></div><div class="fact"><span>Auslauf</span><b id="outlet">Wird geprüft</b></div><div class="fact"><span>Becher</span><b id="cup">Wird geprüft</b></div></div><div class="row section"><button id="refresh" class="secondary">Beleuchtete Prüfung</button><button id="align" class="secondary">Maschine neu suchen</button></div><div class="notice" id="safety">Während des Einlernens sind Kaffeeaktionen gesperrt. Neue Becherprofile starten kein Getränk.</div></section>
<details id="diagnostics"><summary>Was wird gerade erkannt?</summary><p>Vergleichswerte beschreiben Bildähnlichkeit, keine garantierte Trefferwahrscheinlichkeit.</p><div class="diag" id="diag"></div><h3 class="section">Letzte Aktionen</h3><ul class="events" id="events"></ul></details></div>
<div><section class="card"><h2>Becher einlernen</h2><div id="selection"><label class="field" for="profile">Welcher Becher?</label><select id="profile"></select><div id="nameWrap" hidden><label class="field" for="name">Name des neuen Bechers</label><input id="name" maxlength="60" placeholder="Zum Beispiel: Blaue Lieblingstasse"></div></div><div class="steps" aria-label="Einlernfortschritt"><i class="step"></i><i class="step"></i><i class="step"></i><i class="step"></i><i class="step"></i></div><div class="instruction" id="instruction">Becher auswählen und loslegen</div><div class="help" id="help">Der Assistent führt dich durch eine leere Fläche und drei Ansichten deines Bechers.</div><div class="progress" id="progress" hidden><i></i></div><div class="actions"><button class="primary" id="primary">Einlernen starten</button><button class="secondary" id="cancel" hidden>Abbrechen</button></div><div class="message" id="message" role="status" aria-live="polite"></div></section>
<section class="card section"><h2>Deine Becher</h2><p class="muted" id="profilesHint">Ein Profil antippen, um es neu einzulernen oder zu verbessern.</p><div class="profiles" id="profiles"></div></section>
<details id="cameraSettings"><summary>Kamera wechseln</summary><p>Neue Kamera zuerst in Home Assistant hinzufügen. Beim Wechsel bleiben alte Profile gesichert. Die neue Perspektive muss vor einer Getränkeaktion geprüft und eingelernt sein.</p><label class="field" for="sourceCamera">Kamera</label><select id="sourceCamera"></select><img id="sourcePreview" class="preview" alt="Vorschau der ausgewählten neuen Kamera"><button class="secondary section" id="applyCamera">Ausgewählte Kamera übernehmen</button></details><details id="setup"><summary>Maschine und Auslauf einrichten</summary><p>Nur bei der ersten Einrichtung oder einem stark veränderten Blickwinkel. Lade ein Bild, drehe es bei Bedarf und ziehe einen Rahmen um die gesamte Maschine. Den Auslauf suchen wir innerhalb dieses Rahmens automatisch.</p><div class="row"><button id="setupLoad" class="secondary">Aktuelles Bild laden</button><button id="setupRotate" class="secondary" disabled>Bild 180° drehen</button></div><canvas id="setupCanvas" width="1280" height="720" hidden aria-label="Maschine mit einem Rahmen markieren"></canvas><p>Beim Bestätigen werden die bisherigen Becher dieser Perspektive gesichert und für die neue Ansicht gesperrt. Danach die Becher neu einlernen.</p><button id="setupSave" class="primary" disabled>Maschinenrahmen bestätigen</button></details></div></div>
<footer>Lokale Bildverarbeitung · Aufnahmen und Profile bleiben in Home Assistant.</footer></main><script>
const $=id=>document.getElementById(id),stages=['empty','front','rotated','offset','review'];
const base=window.location.pathname.endsWith('/')?window.location.pathname:window.location.pathname+'/';
const url=path=>base+path;let current={},sending=false,view='zoom',polling=false,lastJob='',lastImage=0,imagePending=false,lastPreview=0,localError='',setupImage=null,setupRotation=0,setupBox=null,setupStart=null;
const labels={start:'Einlernen starten',capture:'Aufnahme',save:'Profil speichern',cancel:'Einlernen abbrechen',refresh:'Beleuchtete Prüfung',align:'Maschine suchen',select_profile:'Profil auswählen',select_camera:'Kamera auswählen',set_name:'Bechername',apply_camera:'Kamera übernehmen',calibrate:'Maschine einrichten'};
function state(key){return current.entities?.[key]?.state||'unavailable'}function attr(key,name,fallback=null){return current.entities?.[key]?.attributes?.[name]??fallback}function text(id,value){$(id).textContent=value}
function message(value,error=false){text('message',value);$('message').className='message'+(error?' error':'')}
async function api(path,options={}){let response;try{response=await fetch(url(path),{cache:'no-store',...options})}catch(e){throw Error('Die Verbindung zur App ist unterbrochen. Es wird automatisch erneut versucht.')}
 let data;try{data=await response.json()}catch(e){throw Error('Die App hat keine gültige Antwort erhalten (HTTP '+response.status+').')}
 if(!response.ok)throw Error(data.message||({forbidden:'Diese App-Aktion wurde verweigert.',content_length_required:'Übertragungsfehler der App. Bitte Version prüfen.',invalid_action:'Die App konnte diese Anfrage nicht verarbeiten.',home_assistant_unavailable:'Home Assistant konnte diese Aktion nicht ausführen.'}[data.error])||'Aktion fehlgeschlagen (HTTP '+response.status+').');return data}
function fillSelect(el,options,value){if([...el.options].map(x=>x.value).join('\u0001')!==options.join('\u0001')){el.replaceChildren(...options.map(v=>{let o=document.createElement('option');o.value=v;o.textContent=v;return o}))}el.value=value}
async function act(action,extra={}){if(sending||current.job?.state==='running')return;sending=true;localError='';message('Wird ausgeführt …');render();try{let r=await api('app/action',{method:'POST',headers:{'Content-Type':'application/json','X-Tassenerkennung-App':'1'},body:JSON.stringify({action,...extra})});current.job=r.job}catch(e){localError=e.message;message(e.message,true)}finally{sending=false;await update()}}
function render(){let ready=current.ready,active=state('learning')==='on',stage=attr('learning','schritt','idle'),index=stages.indexOf(stage),busy=sending||current.job?.state==='running',tracking=attr('status','maschine',{}),profiles=attr('status','profile',[]),actualCamera=attr('status','kamera','');
 text('version','App '+(current.app_version||'—')+' · Erkennung '+attr('status','version','—'));text('mainStatus',current.connected?state('status'):'Home Assistant ist nicht erreichbar');
 text('machine',tracking.ok?'Gefunden':'Wird gesucht');$('machine').className=tracking.ok?'good':'warn';text('outlet',tracking.confirmed?'Bestätigt':tracking.ok?'Wird bestätigt':'Noch unklar');$('outlet').className=tracking.confirmed?'good':'warn';let cupKey=state('type'),name=(profiles.find(p=>p.id===cupKey)?.name)||({leer:'Keine Tasse',unbekannt:'Typ wird geprüft',unavailable:'Noch nicht geprüft',kaffeetasse:'Kaffeetasse',babybecher:'Babybecher',latteglas:'Latteglas',espressoglas:'Espressoglas'}[cupKey])||cupKey;text('cup',(attr('type','anzeige_gehalten',false)?'Zuletzt: ':'')+name);$('cup').className=attr('type','aktuell_bestaetigt',false)&&cupKey!=='leer'?'good':'warn';
 let angle=Number(tracking.angle||0),turn=Math.abs(angle)>135?' · automatisch aufrecht gedreht':'';text('caption',tracking.confirmed?'Maschine und Auslauf werden visuell verfolgt. Der Zoom folgt der Maschine'+turn+'.':state('status'));
 fillSelect($('profile'),attr('profile','options',[]),state('profile'));$('profile').disabled=active||busy||!ready;$('nameWrap').hidden=state('profile')!=='+ Neuer Becher';$('name').disabled=active||busy||!ready;if(!busy&&document.activeElement!==$('name'))$('name').value=['unknown','unavailable'].includes(state('name'))?'':state('name');
 const instructions={empty:['Abstellfläche vollständig leeren','Entferne alle Becher unter dem Auslauf. Danach nehmen wir drei frische Bilder auf.'],front:['Becher mittig unter den Auslauf stellen','Die Öffnung muss direkt unter den Düsen liegen. Hände aus dem Bild nehmen und dann aufnehmen.'],rotated:['Becher etwa eine Vierteldrehung drehen','Becher mittig lassen. So lernen wir eine zweite Ansicht.'],offset:['Henkel oder Becher anders ausrichten','Wieder mittig unter dem Auslauf lassen. Das ist die letzte Ansicht.'],review:['Aufnahmen vollständig','Beim Speichern wird das Profil gesichert. Eine vorhandene Version bleibt als private Sicherung erhalten.']};
 let needsSetup=ready&&(!attr('status','referenz_vorhanden',false)||(attr('status','nachfuehrung')==='fehlgeschlagen'&&!tracking.ok));let pair=active?instructions[stage]||['Einlernen läuft',state('learning_status')]:needsSetup?['Maschine zuerst einrichten','Ein Bild laden und die ganze Maschine markieren. Die Auslaufdüsen werden automatisch gesucht.']:!tracking.confirmed?['Maschine wird gesucht','Die Maschine muss vollständig sichtbar sein. Bei einem neuen Blickwinkel kannst du sie unten neu einrichten.']:['Becher auswählen und loslegen','Leere Fläche, drei Becheransichten, speichern. Die Beleuchtung wird automatisch vorbereitet.'];text('instruction',pair[0]);text('help',attr('learning_status','fehler')||pair[1]);$('help').className='help'+(attr('learning_status','fehler')?' error':'');
 document.querySelectorAll('.step').forEach((el,i)=>el.className='step'+(index>i?' done':index===i?' active':''));let progress=attr('learning_status','teilaufnahmen',0);$('progress').hidden=!busy&&!attr('learning_status','aufnahmelauf',false);$('progress').firstElementChild.style.width=Math.min(100,progress/3*100)+'%';
 $('primary').textContent=busy?'Bitte warten …':needsSetup?'Maschine einrichten':!active?'Einlernen starten':stage==='review'?'Profil speichern':'Jetzt 3 Bilder aufnehmen';$('primary').disabled=busy||!ready||(!active&&!needsSetup&&!tracking.confirmed)||(active&&stage!=='review'&&!tracking.confirmed);$('primary').dataset.action=needsSetup?'setup_ui':!active?'start':stage==='review'?'save':'capture';$('cancel').hidden=!active;$('cancel').disabled=busy||!ready;
 $('refresh').disabled=busy||!ready;$('align').disabled=busy||active||!ready;$('setupLoad').disabled=busy||active||!ready;$('setupRotate').disabled=busy||active||!setupImage;$('setupSave').disabled=busy||active||!ready||!setupBox;
 let job=current.job;if(job?.state==='running')message((labels[job.action]||'Aktion')+' läuft'+(job.action==='capture'?' · '+progress+' von 3 Bildern':' …'));else if(job&&job.id!==lastJob){lastJob=job.id;localError=job.state==='error'?job.message:'';message(job.state==='error'?job.message:job.action==='save'?'Profil gespeichert.':job.action==='cancel'?'Einlernen abgebrochen. Beleuchtung wiederhergestellt.':'',job.state==='error')}else if(localError)message(localError,true);
 let key=JSON.stringify(profiles.map(p=>[p.id,p.name,p.samples,p.trained]));if($('profiles').dataset.key!==key){$('profiles').dataset.key=key;$('profiles').replaceChildren(...profiles.map(p=>{let b=document.createElement('button');b.className='profile';let title=document.createElement('b');title.textContent=p.name;let detail=document.createElement('small');detail.textContent=p.trained?p.samples+' Beispielbilder · eingelernt':'Neu einlernen';detail.className=p.trained?'good':'warn';b.append(title,detail);b.onclick=()=>act('select_profile',{option:p.name});return b}))}document.querySelectorAll('.profile').forEach(b=>b.disabled=active||busy||!ready);
 text('profilesHint',profiles.some(p=>!p.trained)?'Für die neue Perspektive fehlende Becher bitte neu einlernen. Alte Daten bleiben gesichert.':'Ein Profil antippen, um weitere Ansichten zu ergänzen.');fillSelect($('sourceCamera'),attr('source_camera','options',[]),state('source_camera'));$('sourceCamera').disabled=active||busy||!ready;$('applyCamera').disabled=active||busy||!ready||state('source_camera')===actualCamera;
 const age=Date.now()-Date.parse(attr('status','bildzeitpunkt',''));text('diag','Bildalter: '+(Number.isFinite(age)?Math.max(0,age/1000).toFixed(1)+' s':'unbekannt')+'\nNachführung: '+(tracking.verified_by==='machine_features'?'Merkmale + Auslauf':tracking.ok?'Struktur + Auslauf':'Suche')+'\nMaschinenvergleich: '+(tracking.body_score??'—')+'\nAuslaufvergleich: '+(tracking.outlet_score??'—')+'\nBildverarbeitung: '+attr('status','gesamtzeit_ms','—')+' ms\nProfilvergleich: '+JSON.stringify(attr('type','vergleichswerte',{}))+'\nHinweis: '+(attr('type','ablehnungsgrund')||'Kein aktueller Ablehnungsgrund'));
 $('events').replaceChildren(...(current.events||[]).slice(0,8).map(e=>{let li=document.createElement('li');li.textContent=new Date(e.finished||e.started).toLocaleTimeString('de-DE')+' · '+(labels[e.action]||e.action)+' · '+(e.state==='error'?e.message:'Abgeschlossen');return li}));
 if($('cameraSettings').open&&Date.now()-lastPreview>4000){$('sourcePreview').src=url('app/source-preview?t='+Date.now());lastPreview=Date.now()}
}
function camera(){if(imagePending&&Date.now()-lastImage>12000)imagePending=false;if(imagePending||document.hidden||Date.now()-lastImage<1000)return;imagePending=true;lastImage=Date.now();$('camera').src=url((view==='tracking'?'app/tracking':view==='raw'?'app/source-preview':'app/camera')+'?t='+lastImage)}
async function update(){if(polling||document.hidden)return;polling=true;try{let data=await api('app/status');current=data;render();camera()}catch(e){text('mainStatus',e.message);message(e.message,true)}finally{polling=false}}
$('camera').onload=()=>{imagePending=false;text('imageAge','Live · '+new Date().toLocaleTimeString('de-DE'))};$('camera').onerror=()=>{imagePending=false;text('imageAge','Bild wird erneut angefordert')};
$('primary').onclick=()=>{if($('primary').dataset.action==='setup_ui'){$('setup').open=true;$('setup').scrollIntoView({behavior:'auto'});$('setupLoad').click()}else act($('primary').dataset.action,{name:$('name').value})};$('cancel').onclick=()=>act('cancel');$('refresh').onclick=()=>act('refresh');$('align').onclick=()=>act('align');$('profile').onchange=e=>act('select_profile',{option:e.target.value});$('sourceCamera').onchange=e=>act('select_camera',{option:e.target.value});$('applyCamera').onclick=()=>act('apply_camera');$('cameraSettings').ontoggle=()=>update();
for(let b of document.querySelectorAll('[data-view]'))b.onclick=()=>{view=b.dataset.view;document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('selected',x===b));imagePending=false;lastImage=0;camera()};function drawSetup(){if(!setupImage)return;let c=$('setupCanvas'),ctx=c.getContext('2d');c.width=setupImage.naturalWidth;c.height=setupImage.naturalHeight;ctx.save();if(setupRotation===180){ctx.translate(c.width,c.height);ctx.rotate(Math.PI)}ctx.drawImage(setupImage,0,0,c.width,c.height);ctx.restore();if(setupBox){ctx.strokeStyle='#66d7b4';ctx.lineWidth=Math.max(2,c.width/300);ctx.strokeRect(setupBox[0]*c.width,setupBox[1]*c.height,(setupBox[2]-setupBox[0])*c.width,(setupBox[3]-setupBox[1])*c.height)}}
$('setupLoad').onclick=()=>{let im=new Image();im.onload=()=>{setupImage=im;setupBox=null;setupRotation=0;$('setupCanvas').hidden=false;drawSetup();render()};im.onerror=()=>message('Das aktuelle Kamerabild konnte nicht geladen werden.',true);im.src=url('app/setup-preview?t='+Date.now())};$('setupRotate').onclick=()=>{setupRotation=setupRotation===0?180:0;setupBox=null;drawSetup();render()};
function setupPoint(e){let r=$('setupCanvas').getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))]}
$('setupCanvas').onpointerdown=e=>{setupStart=setupPoint(e);setupBox=null;$('setupCanvas').setPointerCapture(e.pointerId)};$('setupCanvas').onpointermove=e=>{if(!setupStart)return;let p=setupPoint(e);setupBox=[Math.min(setupStart[0],p[0]),Math.min(setupStart[1],p[1]),Math.max(setupStart[0],p[0]),Math.max(setupStart[1],p[1])];drawSetup()};$('setupCanvas').onpointerup=e=>{setupStart=null;if(setupBox&&(setupBox[2]-setupBox[0]<.04||setupBox[3]-setupBox[1]<.06))setupBox=null;drawSetup();render()};$('setupSave').onclick=()=>act('calibrate',{body:setupBox,rotation:setupRotation});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)update()});update();setInterval(update,1000);
</script></body></html>'''
