"""Locate a known coffee machine and its dispensing head using local visual anchors.

No generic coffee-machine class is claimed: a private, confirmed machine reference
is matched directly. Body geometry and the dispensing head are checked separately.
"""
from collections import OrderedDict
import base64
import hashlib
import time

import cv2
import numpy as np

cv2.setNumThreads(2)
_CACHE = OrderedDict()


def decode(encoded):
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 3_000_000:
        raise ValueError("image_size")
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size > 5_000_000:
        raise ValueError("invalid_image")
    return image


def bootstrap(reference, anchor):
    """Recover the body silhouette and paired nozzles from a confirmed reference."""
    image = decode(reference["camera_context"]["png"])
    h, w = image.shape
    al, at, ar, ab = [int(v*d) for v, d in zip(anchor, (w,h,w,h))]
    aw, ah = ar-al, ab-at
    x0, y0 = max(0,al-int(aw*.25)), max(0,at-int(ah*1.4))
    x1, y1 = min(w,ar+int(aw*.12)), min(h,ab+int(ah*1.05))
    roi = image[y0:y1,x0:x1]
    _,dark=cv2.threshold(cv2.GaussianBlur(roi,(3,3),0),0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((3,3),np.uint8))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(dark)
    sx, sy = min(x1-1,(al+ar)//2)-x0, min(y1-1,(at+ab)//2)-y0
    index = int(labels[sy,sx])
    if not index or stats[index,cv2.CC_STAT_AREA] < aw*ah*.35:
        raise ValueError("machine_reference_not_distinct")
    x,y,mw,mh,_ = map(int,stats[index])
    points=np.argwhere(labels==index)
    upper=points[points[:,0]<y+max(30,round(ah*1.05))]
    # The counter can join the dark body at the base. Its top silhouette cannot.
    ux0,ux1=int(np.quantile(upper[:,1],.01)),int(np.quantile(upper[:,1],.99))+1
    x=ux0;mw=ux1-ux0;mh=min(mh,round(mw*1.10));x+=x0;y+=y0
    if mw < 50 or mh < 65 or not .55 < mw/mh < 1.8:
        raise ValueError("machine_shape_not_distinct")
    body=[max(0,x-3),max(0,y-3),min(w,x+mw+3),min(h,y+mh+3)]
    return _from_body(image,body)


def setup(reference,box):
    image=decode(reference["camera_context"]["png"]);h,w=image.shape
    if not isinstance(box,list) or len(box)!=4 or not all(isinstance(v,(int,float)) and np.isfinite(v) for v in box):raise ValueError("Bitte einen gültigen Maschinenrahmen markieren")
    l,t,r,b=box
    if not (0<=l<r<=1 and 0<=t<b<=1) or (r-l)*w<60 or (b-t)*h<60:raise ValueError("Der Maschinenrahmen ist zu klein oder ungültig")
    return _from_body(image,[round(l*w),round(t*h),round(r*w),round(b*h)])


def _from_body(image,body):
    h,w=image.shape
    l,t,r,b=body;mw=r-l;mh=b-t
    left,top,right,bottom=l+round(mw*.55),t+round(mh*.31),r-round(mw*.035),t+round(mh*.61)
    front=image[top:bottom,left:right]
    candidates=[]
    for threshold in (115,130,150,170):
        _,_,parts,centers=cv2.connectedComponentsWithStats((front>threshold).astype(np.uint8))
        small=[(s,c) for s,c in zip(parts[1:],centers[1:])
               if 2<=s[2]<=mw*.07 and 2<=s[3]<=mh*.065 and 4<=s[4]<=mw*mh*.004]
        for i,(a,ca) in enumerate(small):
            for bb,cb in small[i+1:]:
                sep=abs(ca[0]-cb[0]);dy=abs(ca[1]-cb[1])
                if mw*.035<=sep<=mw*.13 and dy<=max(3,mh*.025):
                    cx=left+(ca[0]+cb[0])/2;cy=top+(ca[1]+cb[1])/2
                    cost=abs((cx-l)/mw-.71)+abs((cy-t)/mh-.44)+2*dy/mh+abs(np.log(max(a[4],1)/max(bb[4],1)))*.12
                    candidates.append((cost,cx,cy,sep))
    if not candidates:
        raise ValueError("paired_outlets_not_visible")
    _,cx,cy,sep=min(candidates)
    outlet=[round(cx-max(sep*1.45,mw*.08)),round(cy-mh*.11),
            round(cx+max(sep*1.45,mw*.08)),round(cy+mh*.055)]
    zone=[round(cx-mw*.22),round(cy+mh*.015),round(cx+mw*.22),round(b-mh*.035)]
    return {"version":1,"body":body,"outlet":outlet,"zone":zone,
            "source_size":[w,h],"method":"paired_nozzles_and_machine_template"}


def _features(reference, model):
    encoded=reference["camera_context"]["png"]
    key=hashlib.sha256((encoded+str(model)).encode()).hexdigest()
    if key not in _CACHE:
        image=decode(encoded)
        mask=np.zeros_like(image)
        l,t,r,b=model["body"];mw,mh=r-l,b-t
        mask[t:b,l:r]=255
        # Exclude the changing touchscreen and vessels, keep sides and fascia.
        mask[t+round(mh*.05):t+round(mh*.31),l+round(mw*.61):r-round(mw*.04)]=0
        zl,zt,zr,zb=model["zone"];mask[zt:zb,zl:zr]=0
        sift=cv2.SIFT_create(nfeatures=3500,contrastThreshold=.008,edgeThreshold=15)
        normalized=cv2.createCLAHE(2.,(8,8)).apply(image)
        points,descriptors=sift.detectAndCompute(normalized,mask)
        _CACHE[key]=(image,mask,points,descriptors)
        if len(_CACHE)>4:_CACHE.popitem(last=False)
    return _CACHE[key]


def _project(box,H):
    l,t,r,b=box
    return cv2.perspectiveTransform(np.float32([[l,t],[r,t],[r,b],[l,b]]).reshape(-1,1,2),H).reshape(-1,2)


def _correlation(a,b,mask):
    valid=mask>0
    # Gradient magnitudes tolerate brightness differences and day/night exposure.
    aa=cv2.GaussianBlur(a,(3,3),0).astype(np.float32)
    bb=cv2.GaussianBlur(b,(3,3),0).astype(np.float32)
    ga=cv2.magnitude(cv2.Sobel(aa,cv2.CV_32F,1,0),cv2.Sobel(aa,cv2.CV_32F,0,1))
    gb=cv2.magnitude(cv2.Sobel(bb,cv2.CV_32F,1,0),cv2.Sobel(bb,cv2.CV_32F,0,1))
    x=ga[valid];y=gb[valid];x-=x.mean();y-=y.mean()
    return float(x@y/max(float(np.linalg.norm(x)*np.linalg.norm(y)),1e-6))


def _bezel_mask(shape,model):
    mask=np.zeros(shape,np.uint8);l,t,r,b=model['body'];bw,bh=r-l,b-t
    left,top,right,bottom=l+round(bw*.51),t+round(bh*.015),r,t+round(bh*.38)
    mask[top:bottom,left:right]=255
    inset=max(3,round(bw*.035));mask[top+inset:bottom-inset,left+inset:right-inset]=0
    return mask


def _assess(ref,live,mask,model,H,matches=0,error=0.,method='machine_features'):
    if H.shape!=(3,3) or not np.isfinite(H).all() or np.linalg.det(H)<=0:
        return {"ok":False,"reason":"machine_motion_ambiguous"}
    scale=float(np.hypot(H[0,0],H[1,0]));angle=float(np.degrees(np.arctan2(H[1,0],H[0,0])))
    if not .45<scale<2.2:return {"ok":False,"reason":"machine_motion_ambiguous"}
    mapped=_project(model['body'],H);h,w=live.shape
    if (mapped[:,0]<2).any() or (mapped[:,0]>w-2).any() or (mapped[:,1]<2).any() or (mapped[:,1]>h-2).any():
        return {"ok":False,"reason":"machine_outside_image"}
    canonical=cv2.warpPerspective(live,np.linalg.inv(H),(ref.shape[1],ref.shape[0]))
    body_score=_correlation(ref,canonical,mask)
    bezel_score=_correlation(ref,canonical,_bezel_mask(ref.shape,model))
    bw=model['body'][2]-model['body'][0];bh=model['body'][3]-model['body'][1]
    ol,ot,orr,ob=model['outlet'];template=ref[ot:ob,ol:orr]
    margin_x=max(4,round(bw*.06));margin_y=max(8,round(bh*.13))
    sl,st,sr,sb=max(0,ol-margin_x),max(0,ot-margin_y),min(ref.shape[1],orr+margin_x),min(ref.shape[0],ob+margin_y)
    result=cv2.matchTemplate(canonical[st:sb,sl:sr],template,cv2.TM_CCOEFF_NORMED)
    _,score,_,location=cv2.minMaxLoc(result);dx=sl+location[0]-ol;dy=st+location[1]-ot
    minimum=.50 if method!='machine_features' else .34
    # When the side is obscured, require two separately verified front anchors.
    # The outlet threshold stays stricter than the full-body verification path.
    front_verified=bezel_score>=.60 and score>=.78
    if (body_score<minimum and not front_verified) or score<.70:
        return {"ok":False,"reason":"outlet_not_verified" if score<.70 else "machine_partly_hidden",
                "body_score":round(body_score,3),"outlet_score":round(score,3),"bezel_score":round(bezel_score,3)}
    outlet=[ol+dx,ot+dy,orr+dx,ob+dy]
    zone=list(model['zone']);zone[0]+=dx;zone[2]+=dx;zone[1]+=dy
    if zone[3]-zone[1]<bh*.18:return {"ok":False,"reason":"no_space_below_outlet"}
    return {"ok":True,"matrix":H.tolist(),"body":mapped.tolist(),"outlet":_project(outlet,H).tolist(),
            "zone":_project(zone,H).tolist(),"canonical_zone":zone,"canonical_outlet":outlet,
            "body_score":round(body_score,3),"outlet_score":round(score,3),"matches":matches,
            "bezel_score":round(bezel_score,3),"partly_hidden":body_score<minimum,
            "error_px":round(error,2),"scale":round(scale,3),"angle":round(angle,2),"verified_by":method}


def _local_search(ref,live,mask,model,previous):
    canonical=cv2.warpPerspective(live,np.linalg.inv(previous),(ref.shape[1],ref.shape[0]))
    def edges(image):
        image=cv2.GaussianBlur(image,(3,3),0).astype(np.float32)
        return cv2.magnitude(cv2.Sobel(image,cv2.CV_32F,1,0),cv2.Sobel(image,cv2.CV_32F,0,1))
    l,t,r,b=model['body'];bw,bh=r-l,b-t;margin=round(max(bw,bh)*.4)
    sl,st,sr,sb=max(0,l-margin),max(0,t-margin),min(ref.shape[1],r+margin),min(ref.shape[0],b+margin)
    target=edges(canonical)[st:sb,sl:sr];template=edges(ref)[t:b,l:r]
    front=mask.copy();front[:,:l+round(bw*.48)]=0;front[t+round(bh*.60):,:]=0
    candidates=[]
    for valid in [mask[t:b,l:r],front[t:b,l:r]]:
        for scale in [.85,.925,1.,1.075,1.15]:
            size=(round(bw*scale),round(bh*scale))
            patch=cv2.resize(template,size);weights=cv2.resize(valid,size,interpolation=cv2.INTER_NEAREST)
            response=cv2.matchTemplate(target,patch,cv2.TM_CCORR_NORMED,mask=weights)
            response=np.nan_to_num(response,nan=-1.,posinf=-1.,neginf=-1.)
            _,score,_,point=cv2.minMaxLoc(response)
            if score>.55:
                relative=np.array([[scale,0,sl+point[0]-scale*l],[0,scale,st+point[1]-scale*t],[0,0,1]],np.float64)
                candidates.append((score,previous@relative))
    for _,H in sorted(candidates,key=lambda p:p[0],reverse=True):
        result=_assess(ref,live,mask,model,H,method='structural_template')
        if result.get('ok'):return result
    return None


def track(reference,current_b64,model,previous=None):
    start=time.monotonic();live=decode(current_b64)
    ref,mask,points,descriptors=_features(reference,model)
    prior=previous if previous is not None else reference.get('camera_transform')
    if prior is not None:
        H=np.asarray(prior,np.float64)
        if H.shape==(3,3) and np.isfinite(H).all() and np.linalg.det(H)>0:
            result=_assess(ref,live,mask,model,H,method='fresh_template_verification')
            if result.get('ok'):
                result['tracking_ms']=round((time.monotonic()-start)*1000,1);return result
            result=_local_search(ref,live,mask,model,H)
            if result:
                result['tracking_ms']=round((time.monotonic()-start)*1000,1);return result
    if descriptors is None or len(points)<6:return {"ok":False,"reason":"reference_features_missing"}
    normalized=cv2.createCLAHE(2.,(8,8)).apply(live)
    sift=cv2.SIFT_create(nfeatures=3500,contrastThreshold=.008,edgeThreshold=15)
    live_points,ds=sift.detectAndCompute(normalized,None)
    if ds is None:return {"ok":False,"reason":"machine_not_visible"}
    matches=[pair[0] for pair in cv2.BFMatcher().knnMatch(descriptors,ds,k=2)
             if len(pair)==2 and pair[0].distance<.74*pair[1].distance]
    if len(matches)<7:return {"ok":False,"reason":"machine_not_visible","matches":len(matches)}
    src=np.float32([points[m.queryIdx].pt for m in matches]);dst=np.float32([live_points[m.trainIdx].pt for m in matches])
    affine,inlier_mask=cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=2.5,maxIters=4000,confidence=.995)
    if affine is None:return {"ok":False,"reason":"machine_motion_ambiguous"}
    keep=inlier_mask[:,0].astype(bool);count=int(keep.sum());H=np.vstack((affine,[0,0,1]))
    if count<7 or keep.mean()<.4:return {"ok":False,"reason":"machine_motion_ambiguous","matches":count}
    span=np.ptp(src[keep],axis=0);bw=model['body'][2]-model['body'][0];bh=model['body'][3]-model['body'][1]
    if span[0]<bw*.25 or span[1]<bh*.25:return {"ok":False,"reason":"machine_partly_hidden"}
    error=float(np.median(np.linalg.norm(cv2.perspectiveTransform(src[keep].reshape(-1,1,2),H).reshape(-1,2)-dst[keep],axis=1)))
    result=_assess(ref,live,mask,model,H,count,error)
    result['tracking_ms']=round((time.monotonic()-start)*1000,1)
    return result
