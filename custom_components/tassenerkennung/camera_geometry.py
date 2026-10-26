"""Camera geometry helpers without a native vision dependency in HA Core."""
import base64
from io import BytesIO
import math
import numpy as np
from PIL import Image


def camera_context(image,settings):
    w,h=image.size
    # A private grayscale reference supports camera geometry; matching is masked to static context.
    rect=[round(w*.235),round(h*.055),round(w*.86),round(h*.68)]
    canvas=image.convert('L')
    buffer=BytesIO();canvas.save(buffer,format='PNG')
    l,t,r,b=settings.zone.pixels(image.size)
    a=settings.anchor;al,at,ar,ab=a.pixels(image.size)
    return {'png':base64.b64encode(buffer.getvalue()).decode(),'rect':rect,
            'exclude':[[l,t,r,b],[round(al+(ar-al)*.45),max(0,at-15),ar+5,max(at,ab-8)]],
            'zone':[[l,t],[r,t],[r,b],[l,b]]}


def rectify(content,reference):
    matrix=np.asarray(reference.get('camera_transform'),dtype=float)
    if matrix.shape!=(3,3) or not np.isfinite(matrix).all() or abs(matrix[2,2])<1e-9:
        raise ValueError('invalid_camera_transform')
    matrix=matrix/matrix[2,2]
    if np.linalg.det(matrix)<=0:raise ValueError('invalid_camera_transform')
    with Image.open(BytesIO(content)) as source:
        if list(source.size)!=reference['source_size'] and not reference.get('machine_model'):
            raise ValueError('camera_resolution_changed')
        image=source.convert('RGB').transform(tuple(reference['source_size']),Image.Transform.PERSPECTIVE,
            tuple(matrix.flat)[:8],Image.Resampling.BILINEAR)
        output=BytesIO();image.save(output,format='JPEG',quality=96)
        return output.getvalue()
