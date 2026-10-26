from http.server import HTTPServer
from pathlib import Path
import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'addon'))
from server import create_handler
from web import HomeAssistantClient, ActionJobs


class Detector:
    def detect(self,image,settings):return []


class FakeHomeAssistant:
    def __init__(self):self.actions=[]
    def status(self):
        return {'app_version':'0.4.0','ready':True,'entities':{
            'status':{'state':'Bereit','attributes':{'version':'0.7.0'}},
            'profile':{'state':'Kaffeetasse','attributes':{'options':['Kaffeetasse','+ Neuer Becher']}},
            'source_camera':{'state':'camera.eve','attributes':{'options':['camera.eve','camera.reolink']}},
        }}
    def camera(self,source=False,tracking=False,raw=False):return (b'tracking' if tracking else b'preview' if source else b'processed'),'image/jpeg'
    def action(self,payload):self.actions.append(payload);return {'ok':True,'action':payload['action']}


@pytest.fixture
def app():
    ha=FakeHomeAssistant();server=HTTPServer(('127.0.0.1',0),create_handler(Detector(),'x'*48,ha))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}',ha
    server.shutdown();server.server_close();thread.join(timeout=2)


def get(url,path,ingress=True):
    headers={'X-Ingress-Path':'/api/hassio_ingress/test/'} if ingress else {}
    return urllib.request.urlopen(urllib.request.Request(url+path,headers=headers))


def test_ui_is_ingress_only_and_contains_learning_and_camera_switch(app):
    url,_=app
    with pytest.raises(urllib.error.HTTPError) as error:get(url,'/',False)
    assert error.value.code==404
    with get(url,'/') as response:html=response.read().decode()
    assert 'Becher einlernen' in html and 'Kamera wechseln' in html
    assert 'https://' not in html and 'http://' not in html


def test_filtered_status_and_both_camera_views(app):
    url,_=app
    with get(url,'/app/status') as response:
        result=json.load(response)
    assert result['ready'] and result['entities']['status']['attributes']['version']=='0.7.0'
    with get(url,'/app/camera') as response:assert response.read()==b'processed'
    with get(url,'/app/source-preview') as response:assert response.read()==b'preview'
    with get(url,'/app/tracking') as response:assert response.read()==b'tracking'


def test_action_requires_ingress_and_custom_header(app):
    url,ha=app;data=json.dumps({'action':'select_camera','option':'camera.reolink'}).encode()
    bad=urllib.request.Request(url+'/app/action',data=data,headers={'Content-Type':'application/json'})
    with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(bad)
    assert error.value.code==403 and not ha.actions
    request=urllib.request.Request(url+'/app/action',data=data,headers={
        'Content-Type':'application/json','X-Ingress-Path':'/api/hassio_ingress/test/',
        'X-Tassenerkennung-App':'1'})
    with urllib.request.urlopen(request) as response:
        assert response.status==202
        job=json.load(response)['job'];assert job['action']=='select_camera'
    for _ in range(50):
        with get(url,'/app/status') as response:result=json.load(response)
        if result['job']['state']!='running':break
        time.sleep(.01)
    assert result['job']['state']=='done'
    assert ha.actions==[{'action':'select_camera','option':'camera.reolink'}]


def test_home_assistant_bridge_has_a_fixed_allowlist():
    client=object.__new__(HomeAssistantClient);calls=[]
    client.state=lambda entity:{'state':'x','attributes':{'options':['camera.eve','camera.reolink']}}
    client._service=lambda domain,service,entity,data=None:calls.append((domain,service,entity,data))
    assert client.action({'action':'select_camera','option':'camera.reolink'})['ok']
    assert calls[-1][0:2]==('select','select_option')
    assert client.action({'action':'start'})['ok'] and calls[-1][0:2]==('button','press')
    with pytest.raises(ValueError):client.action({'action':'automation','entity_id':'automation.eine_tasse_kaffee'})
    with pytest.raises(ValueError):client.action({'action':'select_camera','option':'camera.unknown'})


def test_ingress_does_not_stream_json_actions():
    # Supervisor strips Content-Length and uses chunked encoding when streaming.
    # Our bounded management endpoint requires a buffered, length-framed body.
    import yaml
    config=yaml.safe_load((Path(__file__).resolve().parents[1]/'addon'/'config.yaml').read_text())
    assert config['ingress_stream'] is False


def test_unframed_action_reports_body_problem_without_executing(app):
    url,ha=app
    parsed=urllib.parse.urlsplit(url)
    connection=http.client.HTTPConnection(parsed.hostname,parsed.port,timeout=5)
    body=json.dumps({'action':'start'}).encode()
    connection.request('POST','/app/action',body=iter([body]),encode_chunked=True,headers={
        'Content-Type':'application/json','X-Ingress-Path':'/api/hassio_ingress/test/',
        'X-Tassenerkennung-App':'1'})
    response=connection.getresponse()
    assert response.status==411
    assert json.load(response)=={'error':'content_length_required'}
    assert not ha.actions
    connection.close()


def test_job_reports_real_error_and_rejects_concurrent_actions():
    entered=threading.Event();release=threading.Event()
    class Client:
        def action(self,payload):
            entered.set();release.wait(2)
            raise ValueError('Bitte Abstellfläche leeren')
    jobs=ActionJobs(Client());job=jobs.submit({'action':'capture'})
    assert entered.wait(1)
    with pytest.raises(RuntimeError,match='bereits'):jobs.submit({'action':'start'})
    release.set()
    for _ in range(100):
        result=jobs.snapshot()
        if result['job']['state']!='running':break
        time.sleep(.01)
    assert result['job']['id']==job['job']['id']
    assert result['job']['state']=='error' and result['job']['message']=='Bitte Abstellfläche leeren'
    assert len(result['events'])==1


def test_calibration_bridge_is_bounded_to_active_camera_and_fixed_service():
    client=object.__new__(HomeAssistantClient);calls=[]
    client.state=lambda entity:{'state':'Bereit','attributes':{'kamera':'camera.eve'}}
    client._service=lambda domain,service,entity,data=None:calls.append((domain,service,entity,data))
    assert client.action({'action':'calibrate','body':[.3,.2,.6,.7],'rotation':180})['ok']
    assert calls==[('tassenerkennung','calibrate_machine',None,{'camera':'camera.eve','body':[.3,.2,.6,.7],'rotation':180})]
    for box in [[0,0,2,2],[.5,.3,.1,.6],[float('nan'),0,1,1],'http://example.org']:
        with pytest.raises(ValueError):client.action({'action':'calibrate','body':box})
