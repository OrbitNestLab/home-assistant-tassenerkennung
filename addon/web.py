"""Ingress UI and a narrow Home Assistant API bridge for the learning app."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


ENTITIES = {
    "status": "sensor.tassenerkennung_status",
    "type": "sensor.tassenerkennung_bechertyp",
    "cup": "binary_sensor.tassenerkennung_tasse_unter_dem_auslauf",
    "learning": "binary_sensor.tassenerkennung_einlernmodus",
    "learning_status": "sensor.tassenerkennung_einlernstatus",
    "profile": "select.tassenerkennung_einlernprofil",
    "name": "text.tassenerkennung_name_fur_neuen_becher",
    "source_camera": "select.tassenerkennung_quellkamera_fur_wechsel",
}
BUTTONS = {
    "start": "button.tassenerkennung_einlernen_starten",
    "capture": "button.tassenerkennung_aufnahme_bestatigen",
    "save": "button.tassenerkennung_profil_speichern",
    "cancel": "button.tassenerkennung_einlernen_abbrechen",
    "refresh": "button.tassenerkennung_jetzt_prufen",
    "apply_camera": "button.tassenerkennung_ausgewahlte_kamera_ubernehmen",
    "align": "button.tassenerkennung_ausrichtung_bestatigen",
}
SAFE_ATTRIBUTES = {
    "version", "bild_aktuell", "referenz_vorhanden", "kamera_korrigiert", "nachfuehrung",
    "aktuell_bestaetigt", "anzeige_gehalten", "schritt", "status", "profil", "aufnahmen",
    "benoetigte_aufnahmen", "getraenkeaktionen_gesperrt", "options", "friendly_name",
}


class HomeAssistantClient:
    def __init__(self, token=None, base="http://supervisor/core/api"):
        self.token = token or os.environ.get("SUPERVISOR_TOKEN", "")
        self.base = base.rstrip("/")
        if len(self.token) < 20:
            raise ValueError("home_assistant_api_unavailable")

    def _request(self, path, payload=None, accept="application/json"):
        headers={"Authorization":"Bearer "+self.token,"Accept":accept}
        data=None
        if payload is not None:
            data=json.dumps(payload).encode();headers["Content-Type"]="application/json"
        request=urllib.request.Request(self.base+path,data=data,headers=headers)
        with urllib.request.urlopen(request,timeout=12) as response:
            content=response.read(3_000_001)
            if len(content)>3_000_000:raise ValueError("response_too_large")
            return content,response.headers.get_content_type()

    def state(self, entity_id):
        content,_=self._request("/states/"+entity_id)
        data=json.loads(content)
        return {"state":data.get("state","unknown"),
                "attributes":{key:value for key,value in data.get("attributes",{}).items() if key in SAFE_ATTRIBUTES}}

    def status(self):
        result={key:{"state":"unavailable","attributes":{}} for key in ENTITIES}
        try:
            content,_=self._request("/states")
            states={item.get("entity_id"):item for item in json.loads(content)}
            for key,entity_id in ENTITIES.items():
                data=states.get(entity_id,{})
                result[key]={
                    "state":data.get("state","unavailable"),
                    "attributes":{
                        name:value for name,value in data.get("attributes",{}).items()
                        if name in SAFE_ATTRIBUTES
                    },
                }
        except (urllib.error.HTTPError,urllib.error.URLError,ValueError,json.JSONDecodeError):
            pass
        return {"app_version":"0.3.1","entities":result,
                "ready":result["status"]["attributes"].get("version")=="0.6.0"}

    def camera(self,source=False):
        entity="camera.tassenerkennung_kameravorschau" if source else "camera.tassenerkennung_prufbild"
        return self._request("/camera_proxy/"+entity,accept="image/jpeg")

    def _service(self, domain, service, entity_id, data=None):
        payload={"entity_id":entity_id}
        if data:payload.update(data)
        self._request(f"/services/{domain}/{service}",payload)

    def action(self, payload):
        action=str(payload.get("action", ""))
        if action in BUTTONS:
            self._service("button","press",BUTTONS[action]);return {"ok":True,"action":action}
        if action=="select_profile":
            option=str(payload.get("option", ""))[:60]
            options=self.state(ENTITIES["profile"])["attributes"].get("options",[])
            if option not in options:raise ValueError("invalid_profile")
            self._service("select","select_option",ENTITIES["profile"],{"option":option})
            return {"ok":True,"action":action}
        if action=="select_camera":
            option=str(payload.get("option",""))[:100]
            options=self.state(ENTITIES["source_camera"])["attributes"].get("options",[])
            if option not in options:raise ValueError("invalid_camera")
            self._service("select","select_option",ENTITIES["source_camera"],{"option":option})
            return {"ok":True,"action":action}
        if action=="set_name":
            value=str(payload.get("value", "")).strip()[:60]
            if not re.fullmatch(r"[^<>]{0,60}",value):raise ValueError("invalid_name")
            self._service("text","set_value",ENTITIES["name"],{"value":value})
            return {"ok":True,"action":action}
        raise ValueError("invalid_action")


HTML = r'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tassenerkennung</title><style>
:root{color-scheme:dark;--bg:#101923;--card:#192634;--card2:#213243;--line:#33485b;--text:#eef5fa;--muted:#9fb0c0;--mint:#58d3ae;--amber:#ffba64;--red:#ff7d7d;--blue:#6cb4e4}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 90% 0,#16384a 0,transparent 34%),var(--bg);color:var(--text);font:15px system-ui,-apple-system,sans-serif}
main{max-width:1080px;margin:auto;padding:22px}.top{display:flex;align-items:center;gap:14px;margin-bottom:18px}.logo{width:52px;height:52px;border-radius:16px;background:#223748;display:grid;place-items:center;font-size:27px}.top h1{font-size:25px;margin:0}.top p{margin:3px 0 0;color:var(--muted)}.pill{margin-left:auto;padding:7px 11px;border:1px solid var(--line);border-radius:99px;color:var(--muted)}
.grid{display:grid;grid-template-columns:1.25fr .75fr;gap:16px}.card{background:color-mix(in srgb,var(--card) 94%,transparent);border:1px solid #2b4152;border-radius:22px;padding:18px;box-shadow:0 14px 40px #0003}.card h2{font-size:17px;margin:0 0 13px}.camera{width:100%;aspect-ratio:16/10;object-fit:contain;background:#111a24;border-radius:15px;border:1px solid var(--line)}
.status{display:flex;align-items:center;gap:10px;margin:13px 0 4px}.dot{width:10px;height:10px;border-radius:50%;background:var(--amber);box-shadow:0 0 12px currentColor}.dot.ok{background:var(--mint)}.muted{color:var(--muted)}
.steps{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin:14px 0}.step{height:7px;border-radius:9px;background:#314454}.step.done,.step.active{background:var(--mint)}.step.active{box-shadow:0 0 10px #58d3ae88}.instruction{min-height:45px;font-size:18px;line-height:1.35;margin:10px 0 14px}
label{display:block;color:var(--muted);font-size:13px;margin:11px 0 6px}select,input{width:100%;border:1px solid var(--line);background:var(--card2);color:var(--text);border-radius:12px;padding:11px;font:inherit}input:disabled{opacity:.45}
.buttons{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:14px}button{border:0;border-radius:13px;padding:12px 10px;font:600 14px system-ui;background:#2b4255;color:var(--text);cursor:pointer}button.primary{background:var(--mint);color:#08241c}button.warn{background:#55422c;color:#ffdca7}button:disabled{opacity:.35;cursor:not-allowed}.message{margin-top:12px;min-height:20px;color:var(--muted)}.message.error{color:var(--red)}
.profiles{display:flex;gap:7px;flex-wrap:wrap}.profile{border:1px solid var(--line);border-radius:99px;padding:6px 9px;color:var(--muted)}.safety{margin-top:14px;padding:12px;border-radius:14px;background:#142b2b;color:#acdcd0;line-height:1.4}.facts{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:13px}.fact{background:#14212d;border-radius:12px;padding:10px}.fact b{display:block;margin-top:2px}.hidden{display:none}.cameraSetup{margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}.preview{width:100%;max-height:190px;object-fit:contain;background:#111a24;border-radius:12px;margin-top:10px}.smallButtons{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:9px}
@media(max-width:760px){main{padding:13px}.grid{grid-template-columns:1fr}.top h1{font-size:21px}.pill{display:none}.card{border-radius:17px}.buttons{grid-template-columns:1fr}.facts{grid-template-columns:1fr 1fr}}
</style></head><body><main>
<div class="top"><div class="logo">☕</div><div><h1>Tassenerkennung</h1><p>Lokale Becherprofile und sichere Kaffeeautomatik</p></div><div class="pill" id="version">App 0.3.1</div></div>
<div class="grid"><section class="card"><h2>Live-Prüfung</h2><img class="camera" id="camera" alt="Verarbeitetes Kamerabild"><div class="status"><span class="dot" id="dot"></span><b id="mainStatus">Verbindung wird geprüft …</b></div><div class="muted" id="subStatus">Keine Geräteaktion ohne frische Bestätigung</div>
<div class="facts"><div class="fact"><span class="muted">Kamera</span><b id="cameraState">—</b></div><div class="fact"><span class="muted">Becher</span><b id="cupState">—</b></div></div>
<div class="safety">Die App lernt und verwaltet ausschließlich Becherprofile. Neue Profile starten kein Getränk. Während des Einlernens bleiben alle Kaffeeaktionen gesperrt.</div></section>
<section class="card"><h2>Becher einlernen</h2><label for="profile">Profil</label><select id="profile"></select><div id="nameWrap"><label for="name">Name des neuen Bechers</label><input id="name" maxlength="60" placeholder="z. B. Lieblingstasse"></div>
<div class="steps"><i class="step"></i><i class="step"></i><i class="step"></i><i class="step"></i><i class="step"></i></div><div class="instruction" id="instruction">Profil wählen und Einlernen starten.</div>
<div class="buttons"><button class="primary" id="start">Einlernen starten</button><button id="capture">Aktuellen Schritt aufnehmen</button><button class="primary" id="save">Profil speichern</button><button class="warn" id="cancel">Abbrechen</button></div><div class="message" id="message"></div>
<h2 style="margin-top:20px">Verfügbare Profile</h2><div class="profiles" id="profiles"></div>
<div class="cameraSetup"><h2>Kamera wechseln</h2><p class="muted">Neue Kamera zuerst in Home Assistant hinzufügen, hier auswählen und im Vorschaubild prüfen.</p><select id="sourceCamera"></select><img class="preview" id="sourcePreview" alt="Vorschau der ausgewählten Kamera"><div class="smallButtons"><button id="applyCamera">Kamera übernehmen</button><button id="align">Neue Ansicht ausrichten</button></div><p class="muted">Nach dem Wechsel bleiben alte Profile gesichert. Automationen bleiben gesperrt, bis Ausrichtung und benötigte Becher mit der neuen Kamera neu bestätigt wurden.</p></div></section></div></main>
<script>
const $=id=>document.getElementById(id), stages=['empty','front','rotated','offset','review'];let current={},lastSource='',lastSourcePreview=0;
async function api(path,options={}){let r=await fetch(path,{cache:'no-store',...options});if(!r.ok)throw Error('Die App konnte Home Assistant nicht erreichen.');return r.json()}
async function action(action,extra={}){setMessage('Wird ausgeführt …');try{await api('app/action',{method:'POST',headers:{'Content-Type':'application/json','X-Tassenerkennung-App':'1'},body:JSON.stringify({action,...extra})});await update();setMessage('')}catch(e){setMessage(e.message,true)}}
function setMessage(value,error=false){$('message').textContent=value;$('message').className='message'+(error?' error':'')}
function state(key){return current.entities?.[key]?.state||'unavailable'}function attr(key,name,fallback=null){return current.entities?.[key]?.attributes?.[name]??fallback}
function render(){let ready=current.ready,active=state('learning')==='on',stage=attr('learning','schritt','idle'),idx=stages.indexOf(stage);$('version').textContent='App '+current.app_version+' · Erkennung '+(attr('status','version','—'));$('mainStatus').textContent=state('status');$('dot').className='dot'+(ready?' ok':'');$('subStatus').textContent=ready?(active?'Einlernmodus aktiv · Geräteaktionen gesperrt':'Bereit · lokale Auswertung'):'Home Assistant muss Version 0.6 laden';$('cameraState').textContent=attr('status','kamera_korrigiert',false)?'Ansicht angepasst':'Feste Ansicht';$('cupState').textContent=state('type')==='unavailable'?'Keine aktuelle Freigabe':state('type');
let options=attr('profile','options',[]),selected=state('profile');if([...$('profile').options].map(x=>x.value).join('|')!==options.join('|')){$('profile').innerHTML=options.map(x=>`<option>${escapeHtml(x)}</option>`).join('')}$('profile').value=selected;$('profile').disabled=active||!ready;$('nameWrap').className=selected==='+ Neuer Becher'?'':'hidden';$('name').disabled=active||!ready;if(document.activeElement!==$('name'))$('name').value=state('name')==='unknown'?'':state('name');
$('nameWrap').className=selected==='+ Neuer Becher'?'':'hidden';let cameras=attr('source_camera','options',[]),source=state('source_camera');if([...$('sourceCamera').options].map(x=>x.value).join('|')!==cameras.join('|'))$('sourceCamera').innerHTML=cameras.map(x=>`<option>${escapeHtml(x)}</option>`).join('');$('sourceCamera').value=source;$('sourceCamera').disabled=active||!ready;$('applyCamera').disabled=active||!ready;$('align').disabled=active||!ready;let now=Date.now();if(source!==lastSource||now-lastSourcePreview>5000){$('sourcePreview').src='app/source-preview?t='+now;lastSource=source;lastSourcePreview=now}
$('instruction').textContent=active?state('learning_status'):'Profil wählen und Einlernen starten.';document.querySelectorAll('.step').forEach((el,i)=>el.className='step'+(idx>=0&&i<idx?' done':idx===i?' active':''));$('start').disabled=active||!ready;$('capture').disabled=!active||!['empty','front','rotated','offset'].includes(stage);$('save').disabled=!active||stage!=='review';$('cancel').disabled=!active;$('profiles').innerHTML=options.filter(x=>x!=='+ Neuer Becher').map(x=>`<span class="profile">${escapeHtml(x)}</span>`).join('')||'<span class="muted">Noch keine Profile verfügbar</span>'}
function escapeHtml(v){let d=document.createElement('div');d.textContent=v;return d.innerHTML}
async function update(){try{current=await api('app/status');render();if(current.ready&&$('message').textContent==='Die App konnte Home Assistant nicht erreichen.')setMessage('');$('camera').src='app/camera?t='+Date.now()}catch(e){setMessage(e.message,true);$('mainStatus').textContent='Nicht erreichbar';$('dot').className='dot'}}
$('profile').onchange=e=>action('select_profile',{option:e.target.value});$('name').onchange=e=>action('set_name',{value:e.target.value});$('sourceCamera').onchange=e=>action('select_camera',{option:e.target.value});$('applyCamera').onclick=()=>action('apply_camera');$('align').onclick=()=>action('align');$('start').onclick=()=>action('start');$('capture').onclick=()=>action('capture');$('save').onclick=()=>action('save');$('cancel').onclick=()=>action('cancel');update();setInterval(update,1500);
</script></body></html>'''
