"""Conservative crop recovery. No model, pixels, precision, or FPS changes."""
from pathlib import Path
import json,os
import numpy as np

VERSION='bbox-recovery-v1'
def recovery_enabled():
    value=os.environ.get('TBDUB_FACE_RECOVERY')
    if value is not None:
        if value not in ('0','1'):raise ValueError('TBDUB_FACE_RECOVERY must be 0 or 1')
        return value=='1'
    path=Path(__file__).parent/'face_recovery.json'
    return bool(json.loads(path.read_text()).get('enabled',False)) if path.exists() else False

def groups(indices):
    indices=np.asarray(indices,dtype=int)
    return [] if not len(indices) else np.split(indices,np.flatnonzero(np.diff(indices)!=1)+1)

def recover_boxes(base_boxes, original_valid, rows, width, height, fps):
    """Recover missing mesh crops from CURRENT detections, calibrated locally.

    base_boxes are the original smoothed mesh geometry (before process_bbox).
    Never reinterpret detection boxes as dense mesh coordinates.
    """
    boxes=np.asarray(base_boxes,dtype=float).copy();valid=np.asarray(original_valid,dtype=bool)
    n=len(boxes);det=np.zeros((n,4));reliable=np.zeros(n,dtype=bool)
    for i,row in enumerate(rows):
        if 'detection_box' not in row:continue
        x,y,w,h=map(float,row['detection_box'])
        good=(row.get('detector_score',0)>=0.65 and w>=40 and h>=40 and
              np.isfinite([x,y,w,h]).all() and 0<=x+w/2<width and 0<=y+h/2<height)
        det[i]=[x+w/2,y+h/2,w,h];reliable[i]=good
    anchors=np.flatnonzero(valid&reliable);fallback=[];tracked=[];skip=[]
    available=valid.copy()
    for i in np.flatnonzero(~valid):
        if not reliable[i]:continue
        near=anchors[np.abs(anchors-i)<=round(fps*2)]
        # No nearby trustworthy mesh means no trustworthy scale calibration.
        if not len(near):continue
        near=near[np.argsort(np.abs(near-i))[:20]]
        centers=(boxes[near,:2]+boxes[near,2:])/2
        sides=np.max(boxes[near,2:]-boxes[near,:2],axis=1)
        offsets=np.median((centers-det[near,:2])/det[near,2:],axis=0)
        scale=float(np.median(sides/np.max(det[near,2:],axis=1)))
        if not (0.8<=scale<=3.5 and np.max(np.abs(offsets))<=1):continue
        center=det[i,:2]+offsets*det[i,2:];side=min(scale*max(det[i,2:]),width,height)
        boxes[i]=[*list(center-side/2),*list(center+side/2)]
        available[i]=True;fallback.append(int(i))
    # Only bounded, short, small-motion gaps may use temporal box interpolation.
    for gap in groups(np.flatnonzero(~available)):
        a,b=int(gap[0])-1,int(gap[-1])+1
        can_track=(len(gap)<=max(1,round(fps*.4)) and a>=0 and b<n and available[a] and available[b])
        if can_track:
            centers=(boxes[[a,b],:2]+boxes[[a,b],2:])/2
            sides=np.max(boxes[[a,b],2:]-boxes[[a,b],:2],axis=1)
            can_track=np.linalg.norm(centers[1]-centers[0])<=max(sides)*.8 and max(sides)/min(sides)<=1.5
        if can_track:
            for i in gap:
                t=(i-a)/(b-a);boxes[i]=boxes[a]*(1-t)+boxes[b]*t;available[i]=True;tracked.append(int(i))
        else:skip.extend(map(int,gap))
    # Non-rendered frames still need finite placeholder crops for the video model.
    good=np.flatnonzero(available)
    if not len(good):raise RuntimeError('No reliable face track: cannot synthesize a digital-human video.')
    for k in range(4):boxes[:,k]=np.interp(np.arange(n),good,boxes[good,k])
    return boxes.tolist(),{'version':VERSION,'bbox_fallback_frames':fallback,
                           'tracked_frames':tracked,'passthrough_frames':skip}

def seed_boxes(landmarks, rows, width, height, build):
    """Keep the original geometry builder; fill only for its numeric input."""
    valid=np.array([p is not None for p in landmarks],bool);indices=np.flatnonzero(valid)
    if not len(indices):
        # Without a landmark anchor we must not blindly guess a full-frame face crop.
        raise RuntimeError('No reliable facial landmarks in the entire video.')
    points=np.stack([landmarks[i] for i in indices])
    filled=np.empty((len(landmarks),*points.shape[1:]),np.float32)
    for p in range(points.shape[1]):
        for c in range(points.shape[2]):filled[:,p,c]=np.interp(np.arange(len(landmarks)),indices,points[:,p,c])
    return build(filled,width,height),valid

def output_passthrough(indices,start_frame,source_skip):
    """Use the exact generation ping-pong map, including --start-frame."""
    skip=set(source_skip or [])
    return [int(i)+start_frame in skip for i in indices]
