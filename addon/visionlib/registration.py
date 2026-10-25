"""Conservative camera pose registration. Images stay inside the local app."""
import base64
import hashlib
from collections import OrderedDict
import cv2
import numpy as np
from PIL import Image

cv2.setNumThreads(2)
_CACHE=OrderedDict()

def _image(encoded):
    data=base64.b64decode(encoded,validate=True)
    if len(data)>2_000_000:raise ValueError('image_size')
    image=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_GRAYSCALE)
    if image is None or image.size>4_000_000:raise ValueError('invalid_image')
    return image

def _score(frame,landmark,dx,dy):
    ref=np.asarray(landmark['patch'],np.float32);ref=ref-ref.mean()
    l,t,r,b=landmark['rect'];l+=dx;r+=dx;t+=dy;b+=dy
    if l<0 or t<0 or r>frame.shape[1] or b>frame.shape[0]:return -1.
    patch=frame[t:b,l:r].astype(np.float32);patch-=patch.mean()
    return float(np.sum(ref*patch)/max(float(np.linalg.norm(ref)*np.linalg.norm(patch)),1.))

def validate_machine(rectified,landmarks):
    gray=np.asarray(Image.fromarray(rectified).resize((640,round(rectified.shape[0]*640/rectified.shape[1])),Image.Resampling.BILINEAR))
    scores=[]
    for dy in range(-6,7):
        for dx in range(-6,7):
            values=[_score(gray,l,dx,dy) for l in landmarks]
            scores.append((min(values),(dx,dy),values))
    best,shift,values=max(scores,key=lambda v:v[0])
    return best,shift,values

def register_camera(reference,current_b64):
    """Find a global camera warp, then independently check the actual machine."""
    context=reference['camera_context'];key=hashlib.sha256(context['png'].encode()).hexdigest()
    sift=cv2.SIFT_create(nfeatures=8000,contrastThreshold=.005,edgeThreshold=16)
    clahe=cv2.createCLAHE(2.,(8,8))
    if key not in _CACHE:
        image=_image(context['png']);mask=np.zeros(image.shape,np.uint8)
        l,t,r,b=context['rect'];mask[t+4:b-4,l+4:r-4]=255
        for l,t,r,b in context['exclude']:mask[t:b,l:r]=0
        points,descriptors=sift.detectAndCompute(clahe.apply(image),mask)
        _CACHE[key]=(image,points,descriptors)
        if len(_CACHE)>4:_CACHE.popitem(last=False)
    ref,points,descriptors=_CACHE[key]
    live=_image(current_b64)
    if list(live.shape[::-1])!=reference['source_size']:return {'ok':False,'reason':'resolution_changed'}
    kp2,ds2=sift.detectAndCompute(clahe.apply(live),None)
    if descriptors is None or ds2 is None:return {'ok':False,'reason':'insufficient_features'}
    pairs=cv2.BFMatcher().knnMatch(descriptors,ds2,k=2)
    attempts=[]
    for ratio_test in [.75,.70]:
        matches=[a for pair in pairs if len(pair)==2 for a,b in [pair] if a.distance<ratio_test*b.distance]
        result=_verify(reference,ref,live,points,kp2,matches)
        if result.get('ok'):
            result['ratio_test']=ratio_test
            return result
        attempts.append(result.get('reason','rejected'))
    return {'ok':False,'reason':attempts[-1],'attempts':attempts}

def _verify(reference,ref,live,points,kp2,matches):
    context=reference["camera_context"]
    if len(matches)<12:return {'ok':False,'reason':'insufficient_matches','matches':len(matches)}
    src=np.float32([points[m.queryIdx].pt for m in matches]);dst=np.float32([kp2[m.trainIdx].pt for m in matches])
    H,mask=cv2.findHomography(src,dst,cv2.USAC_MAGSAC,2.,maxIters=10000,confidence=.999)
    if H is None:return {'ok':False,'reason':'no_transform'}
    keep=mask[:,0].astype(bool);inliers=int(keep.sum());ratio=float(keep.mean())
    if inliers<12 or ratio<.25:return {'ok':False,'reason':'weak_transform','inliers':inliers,'ratio':ratio}
    span=np.ptp(src[keep],axis=0)
    if span[0]<200 or span[1]<120:return {'ok':False,'reason':'clustered_matches'}
    H=H/H[2,2]
    if not np.isfinite(H).all() or np.linalg.det(H)<=0:return {'ok':False,'reason':'invalid_transform'}
    zone=np.float32(context['zone']).reshape(-1,1,2)
    mapped=cv2.perspectiveTransform(zone,H).reshape(-1,2)
    w,h=live.shape[1],live.shape[0]
    area=abs(cv2.contourArea(mapped));base_area=abs(cv2.contourArea(zone))
    if not .5<=area/max(base_area,1)<=2.0 or not cv2.isContourConvex(mapped):return {'ok':False,'reason':'extreme_perspective'}
    if (mapped[:,0]<4).any() or (mapped[:,1]<4).any() or (mapped[:,0]>w-4).any() or (mapped[:,1]>h-4).any():return {'ok':False,'reason':'outlet_outside_image'}
    # Background matches alone never authorize a camera correction.
    rectified=np.asarray(Image.fromarray(live).transform((ref.shape[1],ref.shape[0]),Image.Transform.PERSPECTIVE,tuple(H.flat)[:8],Image.Resampling.BILINEAR))
    score,offset,values=validate_machine(rectified,reference['landmarks'])
    if score<.86 or max(values)<.90:return {'ok':False,'reason':'machine_not_verified','machine_score':round(score,4)}
    reproj=cv2.perspectiveTransform(src[keep].reshape(-1,1,2),H).reshape(-1,2)
    error=float(np.median(np.linalg.norm(reproj-dst[keep],axis=1)))
    return {'ok':True,'matrix':H.tolist(),'offset':list(offset),'zone':mapped.tolist(),
            'inliers':inliers,'ratio':round(ratio,3),'error_px':round(error,3),'machine_score':round(score,4)}

def verify_camera(reference,current_b64,matrix):
    """Verify one proposed pose on a new image without fitting a different pose."""
    live=_image(current_b64);H=np.asarray(matrix,dtype=np.float64)
    if H.shape!=(3,3) or not np.isfinite(H).all() or abs(H[2,2])<1e-9:return {'ok':False,'reason':'invalid_transform'}
    H=H/H[2,2]
    if np.linalg.det(H)<=0 or list(live.shape[::-1])!=reference['source_size']:return {'ok':False,'reason':'invalid_transform'}
    zone=np.float32(reference['camera_context']['zone']).reshape(-1,1,2);mapped=cv2.perspectiveTransform(zone,H).reshape(-1,2)
    area=abs(cv2.contourArea(mapped))/max(abs(cv2.contourArea(zone)),1)
    w,h=live.shape[1],live.shape[0]
    if not .5<=area<=2 or not cv2.isContourConvex(mapped) or (mapped[:,0]<4).any() or (mapped[:,0]>w-4).any() or (mapped[:,1]<4).any() or (mapped[:,1]>h-4).any():return {'ok':False,'reason':'outlet_outside_image'}
    rectified=np.asarray(Image.fromarray(live).transform(tuple(reference['source_size']),Image.Transform.PERSPECTIVE,tuple(H.flat)[:8],Image.Resampling.BILINEAR))
    score,offset,values=validate_machine(rectified,reference['landmarks'])
    if score<.86 or max(values)<.9:return {'ok':False,'reason':'machine_not_verified','machine_score':round(score,4)}
    return {'ok':True,'matrix':H.tolist(),'offset':list(offset),'zone':mapped.tolist(),'machine_score':round(score,4),'verified_new_frame':True}
