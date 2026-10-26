"""Private calibrated vessel views; conservative rejection, no beverage actions."""
import time
import uuid
import re
import numpy as np
from PIL import Image, ImageFilter

class VesselProfiles:
    @classmethod
    def empty(cls,camera,source_size=None,names=None):
        return cls({'version':3,'camera':camera,'source_size':source_size or [1280,720],
                    'zone_relative_box':[0,0,1,1],'samples':{'leer':[]},'names':names or {},'automatic_beverages':False})

    def __init__(self, data):
        self.data=data
        self.samples={k:[np.asarray(a,dtype=np.float32)/255 for a in arrays] for k,arrays in data['samples'].items()}
        if 'leer' not in self.samples:raise ValueError('invalid_profiles')
        if any(not re.fullmatch(r'[a-z0-9_]{2,50}',key) for key in self.samples):raise ValueError('invalid_profiles')
        self.names={str(k):str(v)[:60] for k,v in data.get('names',{}).items()
                    if re.fullmatch(r'[a-z0-9_]{2,50}',str(k))}
        self.candidate=None;self.count=0;self.since=None;self.last=None
        self.latched=None;self.episode=None
        self.display_label=None;self.display_at=None
        self.current_confirmed=False;self.held=False
        self.last_scores={};self.empty_distance=None;self.rejection_reason=None

    def invalidate(self):
        self.candidate=None;self.count=0;self.since=None;self.last=None
        self.display_label=None;self.display_at=None
        self.current_confirmed=False;self.held=False

    @property
    def lifecycle(self):
        return {"label":self.latched,"episode":self.episode}

    def restore_lifecycle(self,state):
        import re
        if isinstance(state,dict) and state.get("label") in self.samples and state.get("label")!="leer" and re.fullmatch(r"[0-9a-f]{32}",str(state.get("episode",""))):
            self.latched=state["label"];self.episode=state["episode"]

    @staticmethod
    def duration_for(label):
        return 2. if label=="babybecher" else 5. if label=="leer" else 3.

    def _display(self,timestamp):
        self.held=bool(self.display_label and self.display_at is not None and 0<=timestamp-self.display_at<=10)
        return self.display_label if self.held else "unbekannt"

    @staticmethod
    def similarity(a,b):
        flat_a=a.reshape(-1);flat_b=b.reshape(-1)
        cosine=float(flat_a@flat_b/max(float(np.linalg.norm(flat_a)*np.linalg.norm(flat_b)),1e-9))
        ca=a.mean(axis=(0,1));cb=b.mean(axis=(0,1))
        ca=ca/max(float(ca.sum()),1e-6);cb=cb/max(float(cb.sum()),1e-6)
        return cosine,float(np.linalg.norm(ca-cb))

    def display_name(self,key):
        defaults={'babybecher':'Babybecher','kaffeetasse':'Kaffeetasse','leer':'Leere Abstellfläche',
                  'latteglas':'Latteglas','espressoglas':'Espressoglas'}
        return self.names.get(key,defaults.get(key,key.replace('_',' ').title()))

    def extract_sample(self,image,settings):
        box=self.box(image,settings)
        size=(48,56) if self.data.get('version',2)>=3 else (26,28)
        patch=image.crop(tuple(round(value) for value in box)).resize(size,Image.Resampling.BILINEAR)
        return np.asarray(patch,dtype=np.uint8).tolist()

    def add_samples(self,key,name,samples):
        if not re.fullmatch(r'[a-z0-9_]{2,50}',key) or key=='leer':raise ValueError('invalid_profile')
        converted=[np.asarray(sample,dtype=np.float32)/255 for sample in samples]
        shape=(56,48,3) if self.data.get('version',2)>=3 else (28,26,3)
        if not converted or any(array.shape!=shape for array in converted):raise ValueError('invalid_samples')
        existing=self.samples.get(key,[])
        self.samples[key]=(existing+converted)[-12:]
        self.names[key]=name[:60]
        self.invalidate()

    def reset_for_camera(self,camera,source_size,empty_samples):
        converted=[np.asarray(sample,dtype=np.float32)/255 for sample in empty_samples]
        shape=(56,48,3) if self.data.get('version',2)>=3 else (28,26,3)
        if len(converted)<3 or any(array.shape!=shape for array in converted):raise ValueError('invalid_empty_samples')
        self.samples={'leer':converted[-6:]}
        self.data={**self.data,'camera':camera,'source_size':list(source_size),'version':max(2,self.data.get('version',2)),
                   'notes':'Profiles reset safely after source-camera change; previous data is in the private backup store.'}
        self.invalidate();self.latched=None;self.episode=None

    def export(self):
        data={k:v for k,v in self.data.items() if k not in {'samples','names'}}
        data['version']=max(2,self.data.get('version',2))
        data['names']=dict(self.names)
        data['samples']={key:[np.clip(array*255,0,255).astype(np.uint8).tolist() for array in arrays]
                         for key,arrays in self.samples.items()}
        return data

    def box(self,image,settings):
        l,t,r,b=settings.zone.pixels(image.size)
        x1,y1,x2,y2=self.data["zone_relative_box"]
        return (l+x1*(r-l),t+y1*(b-t),l+x2*(r-l),t+y2*(b-t))

    def classify(self,image,settings):
        if settings.camera!=self.data['camera'] or list(image.size)!=self.data['source_size']:
            return 'unbekannt',0.
        if self.data.get('version',2)>=3:return self._classify_v3(image,settings)
        box=self.box(image,settings)
        scores={key:0. for key in self.samples}
        for dx in range(-9,10,3):
            for dy in range(-9,10,3):
                patch=image.crop((round(box[0]+dx),round(box[1]+dy),round(box[2]+dx),round(box[3]+dy))).resize((26,28),Image.Resampling.BILINEAR)
                a=np.asarray(patch,dtype=np.float32)/255
                if a.mean()<.045:continue
                for key,templates in self.samples.items():
                    for template in templates:
                        similarity,chroma=self.similarity(a,template)
                        if chroma <= .045:scores[key]=max(scores[key],similarity)
        ranked=sorted(scores.items(),key=lambda x:x[1],reverse=True)
        label,score=ranked[0]
        if score<.95 or (len(ranked)>1 and score-ranked[1][1]<.06):return 'unbekannt',score
        return label,score

    def evaluate(self,image,settings,timestamp,allowed,has_vessel=None):
        self.current_confirmed=False;self.held=False
        if not allowed:
            self.invalidate();return 'unbekannt',0.,self.episode
        label,score=self.classify(image,settings)
        if (label=='leer' and has_vessel is True) or (label not in ['leer','unbekannt'] and has_vessel is False):
            label='unbekannt';self.rejection_reason='Bildvergleich und Objekterkennung stimmen nicht überein'
        if label=='unbekannt':
            # One soft miss does not erase the preceding valid observations.
            if self.last is None or timestamp-self.last>2.5:
                self.candidate=None;self.count=0;self.since=None;self.last=None
            return self._display(timestamp),score,self.episode
        if label!=self.candidate or self.last is None or not 0<timestamp-self.last<=max(3,settings.interval*2.5):
            self.candidate=label;self.since=timestamp;self.count=0
        self.last=timestamp;self.count+=1
        if self.count<3 or timestamp-self.since<self.duration_for(label):
            return self._display(timestamp),score,self.episode
        self.current_confirmed=True;self.display_label=label;self.display_at=timestamp
        if label=='leer':self.latched=None;self.episode=None
        elif label!=self.latched:self.latched=label;self.episode=uuid.uuid4().hex
        return label,score,self.episode

    def profile_details(self,camera=None):
        keys=set(self.names)|set(self.samples)|{'kaffeetasse','babybecher','latteglas','espressoglas'}
        return [{'id':key,'name':self.display_name(key),'samples':len(self.samples.get(key,[])),
                 'trained':len(self.samples.get(key,[]))>=3 and (camera is None or camera==self.data.get('camera')),'format':self.data.get('version',2)}
                for key in sorted(keys) if key!='leer']

    def set_empty(self,samples):
        shape=(56,48,3) if self.data.get('version',2)>=3 else (28,26,3)
        converted=[np.asarray(a,dtype=np.float32)/255 for a in samples]
        if len(converted)<3 or any(a.shape!=shape for a in converted):raise ValueError('invalid_empty_samples')
        self.samples['leer']=converted[-6:];self.invalidate()

    @staticmethod
    def _foreground(sample,empty):
        residual=sample-empty
        border=np.concatenate([residual[:3].reshape(-1,3),residual[-3:].reshape(-1,3),
                               residual[:,:3].reshape(-1,3),residual[:,-3:].reshape(-1,3)])
        offset=np.clip(np.median(border,axis=0),-.15,.15)
        residual-=offset
        magnitude=np.sqrt(np.mean(residual**2,axis=2))
        binary=Image.fromarray((magnitude>.065).astype(np.uint8)*255).filter(ImageFilter.MedianFilter(3))
        return np.asarray(binary)>0,residual

    @classmethod
    def appearance_score(cls,a,b,empty):
        ma,ra=cls._foreground(a,empty);mb,rb=cls._foreground(b,empty)
        union=ma|mb;intersection=ma&mb
        if union.sum()<25:return 0.
        overlap=float(intersection.sum()/union.sum())
        x=ra[union].reshape(-1);y=rb[union].reshape(-1)
        cosine=max(0.,float(x@y/max(np.linalg.norm(x)*np.linalg.norm(y),1e-6)))
        color=max(0.,1-float(np.linalg.norm(a[ma].mean(0)-b[mb].mean(0)))/.7) if ma.any() and mb.any() else 0.
        return .50*overlap+.30*cosine+.20*color

    def _classify_v3(self,image,settings):
        if not self.samples.get('leer'):
            self.rejection_reason='Leere Abstellfläche muss eingelernt werden';return 'unbekannt',0.
        empty=np.median(np.stack(self.samples['leer']),axis=0)
        sample=np.asarray(self.extract_sample(image,settings),np.float32)/255
        mask,residual=self._foreground(sample,empty)
        fraction=float(mask.mean());distance=float(np.mean(np.abs(residual)));self.empty_distance=distance
        if fraction<.02 and distance<.025:
            self.last_scores={'leer':1-min(1,distance/.1)};self.rejection_reason=None
            return 'leer',self.last_scores['leer']
        scores={}
        for key,templates in self.samples.items():
            if key=='leer':continue
            scores[key]=max((self.appearance_score(sample,t,empty) for t in templates),default=0.)
        self.last_scores={k:round(v,3) for k,v in scores.items()}
        ranked=sorted(scores.items(),key=lambda p:p[1],reverse=True)
        if not ranked:self.rejection_reason='Noch kein Becherprofil gelernt';return 'unbekannt',0.
        label,score=ranked[0];second=ranked[1][1] if len(ranked)>1 else 0.
        # A profile still needs a detected vessel, three fresh confirmations and
        # a clear lead over every other learned profile. 0.75 tolerates the
        # small exposure changes seen with the same real cup under task lighting.
        if fraction<.025 or score<.75:
            self.rejection_reason='Becher passt noch nicht sicher zu einem Profil';return 'unbekannt',score
        if score-second<.08:
            self.rejection_reason='Zwei Becherprofile sehen zu ähnlich aus';return 'unbekannt',score
        self.rejection_reason=None;return label,score
