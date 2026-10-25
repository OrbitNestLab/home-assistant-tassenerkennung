"""Two independent landmarks; bounded translation only, never replace the baseline."""
from dataclasses import replace
import numpy as np
from .vision import Alignment, Rect, gray_frame


def landmark_rects(settings):
    a = settings.anchor
    w, h = a.right-a.left, a.bottom-a.top
    return [Rect(a.left, a.top, a.left+w*.42, a.bottom),
            Rect(a.left+w*.55, a.top+h*.6, a.right, a.bottom)]


def add_landmarks(reference, image, settings):
    gray = gray_frame(image)
    landmarks = []
    for rect in landmark_rects(settings):
        l,t,r,b = rect.pixels((gray.shape[1],gray.shape[0]))
        patch = gray[t:b,l:r]
        if patch.size < 60 or patch.std() < 8:
            raise ValueError('reference_not_distinct')
        landmarks.append({'rect':[l,t,r,b], 'patch':patch.astype(np.uint8).tolist()})
    return {**reference, 'landmarks':landmarks, 'offset':[0,0]}


def match(gray, landmark, center=(0,0), radius=(4,4), step=1):
    ref = np.asarray(landmark['patch'],dtype=np.float32)
    ref -= ref.mean(); norm=float(np.linalg.norm(ref))
    l,t,r,b=landmark['rect']; scores=[]
    if norm < 1:return -1.,(0,0),0.
    for dy in range(center[1]-radius[1],center[1]+radius[1]+1,step):
        for dx in range(center[0]-radius[0],center[0]+radius[0]+1,step):
            if l+dx<0 or t+dy<0 or r+dx>gray.shape[1] or b+dy>gray.shape[0]:continue
            patch=gray[t+dy:b+dy,l+dx:r+dx]; patch=patch-patch.mean()
            score=float(np.sum(ref*patch)/(norm*max(float(np.linalg.norm(patch)),1)))
            scores.append((score,(dx,dy)))
    if not scores:return -1.,(0,0),0.
    best,shift=max(scores)
    competing=max((s for s,p in scores if max(abs(p[0]-shift[0]),abs(p[1]-shift[1]))>6),default=-1.)
    return best,shift,best-competing


def locate(image, settings, reference, recovery=False):
    if not reference or reference.get('fingerprint')!=settings.fingerprint:
        return Alignment(False,'Ausrichtung bestätigen')
    if reference.get('source_size')!=list(image.size):
        return Alignment(False,'Bildgröße geändert – Ausrichtung prüfen')
    landmarks=reference.get('landmarks')
    if not landmarks or len(landmarks)!=2:
        return Alignment(False,'Zweite Positionsreferenz bestätigen')
    gray=gray_frame(image); offset=tuple(reference.get('offset',[0,0])); found=[]
    if reference.get('camera_transform') and not recovery:
        # A shared displacement avoids selecting different repeated panel edges.
        candidates=[]
        for dy in range(offset[1]-3,offset[1]+4):
            for dx in range(offset[0]-3,offset[0]+4):
                values=[match(gray,l,(dx,dy),(0,0))[0] for l in landmarks]
                candidates.append((min(values),(dx,dy),max(values)))
        score,shift,strongest=max(candidates,key=lambda x:x[0])
        if score>=.86 and strongest>=.90:
            return Alignment(True,'Kameraansicht und Auslauf bestätigt',score,shift)
        return Alignment(False,'Kameraansicht nicht sicher bestätigt',score,shift)

    for landmark in landmarks:
        if recovery:
            score,shift,margin=match(gray,landmark,(0,0),(60,40),step=2)
            fine_score,fine_shift,_=match(gray,landmark,shift,(2,2))
            found.append((fine_score,fine_shift,margin+(fine_score-score)))
        else:
            found.append(match(gray,landmark,offset,(3,3)))
    score=min(x[0] for x in found); shifts=[x[1] for x in found]
    agreed=max(abs(shifts[0][i]-shifts[1][i]) for i in (0,1))<=2
    shift=tuple(round(sum(s[i] for s in shifts)/2) for i in (0,1))
    if not agreed or score < (.92 if recovery else .86):
        return Alignment(False,'Maschine oder Auslauf nicht eindeutig erkannt',score,shift)
    if recovery and min(x[2] for x in found)<.035:
        return Alignment(False,'Mehrdeutige Position – Ausrichtung prüfen',score,shift)
    if not recovery and max(abs(shift[i]-offset[i]) for i in (0,1))>2:
        return Alignment(False,'Maschine verschoben – Nachführung erforderlich',score,shift)
    try: shifted_settings(settings,reference,shift)
    except ValueError:return Alignment(False,'Auslauf außerhalb des Prüfbereichs',score,shift)
    return Alignment(True,'Maschine und Auslauf bestätigt',score,shift)


def shifted_settings(settings,reference,offset=None):
    offset=offset if offset is not None else reference.get('offset',[0,0])
    source=reference['source_size']; width=min(640,source[0]); height=round(source[1]*width/source[0])
    dx,dy=offset[0]/width,offset[1]/height
    def shift(r):return Rect(r.left+dx,r.top+dy,r.right+dx,r.bottom+dy)
    return replace(settings,crop=shift(settings.crop),zone=shift(settings.zone),anchor=shift(settings.anchor))
