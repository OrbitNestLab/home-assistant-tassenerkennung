from http.server import HTTPServer
from pathlib import Path
import sys,threading,urllib.request,urllib.error,json
from io import BytesIO
import pytest
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'addon'))
from server import create_handler
from visionlib.vision import Detection

@pytest.fixture
def api():
 class Detector:
  def detect(self,image,settings):return [Detection((5.,8.,80.,100.),.85)]
 server=HTTPServer(('127.0.0.1',0),create_handler(Detector(),'x'*48))
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 yield f'http://127.0.0.1:{server.server_port}'
 server.shutdown();server.server_close();thread.join(timeout=2)

def test_api_auth_and_no_reflected_secret(api):
 with urllib.request.urlopen(api+'/health') as response:assert json.load(response)['ready']
 request=urllib.request.Request(api+'/detect',data=b'abc')
 with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
 assert error.value.code==401
 assert b'xxx' not in error.value.read()

def test_valid_image_and_invalid_threshold(api):
 buffer=BytesIO();Image.new('RGB',(200,200),'gray').save(buffer,format='PNG')
 request=urllib.request.Request(api+'/detect?confidence=.6',data=buffer.getvalue(),headers={'Authorization':'Bearer '+'x'*48})
 with urllib.request.urlopen(request) as response:result=json.load(response)
 assert result['protocol']==1 and len(result['detections'])==1
 bad=urllib.request.Request(api+'/detect?confidence=nan',data=buffer.getvalue(),headers={'Authorization':'Bearer '+'x'*48})
 with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(bad)
 assert error.value.code==400
