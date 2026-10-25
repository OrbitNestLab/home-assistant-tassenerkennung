from http.server import HTTPServer
from pathlib import Path
import json
import sys
import threading
import urllib.error
import urllib.request

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'addon'))
from server import create_handler
from web import HomeAssistantClient


class Detector:
    def detect(self,image,settings):return []


class FakeHomeAssistant:
    def __init__(self):self.actions=[]
    def status(self):
        return {'app_version':'0.3.1','ready':True,'entities':{
            'status':{'state':'Bereit','attributes':{'version':'0.6.0'}},
            'profile':{'state':'Kaffeetasse','attributes':{'options':['Kaffeetasse','+ Neuer Becher']}},
            'source_camera':{'state':'camera.eve','attributes':{'options':['camera.eve','camera.reolink']}},
        }}
    def camera(self,source=False):return (b'preview' if source else b'processed'),'image/jpeg'
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
    assert result['ready'] and result['entities']['status']['attributes']['version']=='0.6.0'
    with get(url,'/app/camera') as response:assert response.read()==b'processed'
    with get(url,'/app/source-preview') as response:assert response.read()==b'preview'


def test_action_requires_ingress_and_custom_header(app):
    url,ha=app;data=json.dumps({'action':'select_camera','option':'camera.reolink'}).encode()
    bad=urllib.request.Request(url+'/app/action',data=data,headers={'Content-Type':'application/json'})
    with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(bad)
    assert error.value.code==403 and not ha.actions
    request=urllib.request.Request(url+'/app/action',data=data,headers={
        'Content-Type':'application/json','X-Ingress-Path':'/api/hassio_ingress/test/',
        'X-Tassenerkennung-App':'1'})
    with urllib.request.urlopen(request) as response:assert json.load(response)['ok']
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
