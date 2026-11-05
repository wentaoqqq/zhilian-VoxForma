"""VoiceStudio extension entrypoint; upstream backend remains independently upgradable."""
from __future__ import annotations
from avatar_paths import smi_path
import asyncio, contextvars, functools, hashlib, json, os, shutil, time, uuid
from pathlib import Path
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from backend.main import app
from api.dependencies import require_consumer, require_admin
from services import model_manager as mm
import gpu_client

from services import video_backend
ROOT=video_backend.root()
from avatar_paths import data_dir, assets_dir
from video_runtime import ensure_queue, queue_url
ASSETS=assets_dir()
BASE=data_dir().parent
QUEUE=queue_url()
os.environ.setdefault('AVATAR_GPU_AUTOSTART','1')

# Raw pool users and guarded pool users share this class. Capture context
# explicitly; Python executors do not automatically propagate ContextVars.
_original_submit=mm._ResilientGpuPool.submit
def _submit(self,fn,/,*args,**kwargs):
    gpu_client.require_lease()
    context=contextvars.copy_context()
    def guarded():
        gpu_client.require_lease()
        return fn(*args,**kwargs)
    return _original_submit(self,context.run,guarded)
mm._ResilientGpuPool.submit=_submit
_original_get_model=mm.get_model
async def _get_model(*args,**kwargs):
    gpu_client.require_lease()
    return await _original_get_model(*args,**kwargs)
mm.get_model=_get_model

class Admission:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type'] not in ('http','websocket'):
            return await self.app(scope,receive,send)
        path=scope['path'];headers=dict(scope.get('headers',[]))
        # Avatar bridge only submits/polls CPU-side work. Its queue executor
        # obtains the lease; holding one while submitting would deadlock.
        passive=path.startswith(('/api/avatar/','/auth/','/batch/','/engines/video/'))
        mutating=scope.get('method','GET') not in ('GET','HEAD','OPTIONS')
        if scope['type']=='websocket':
            # TTS requests on a persistent websocket acquire per message, not
            # per connection. Idle preview sockets must never reserve a GPU.
            if path=='/ws/tts':return await self.app(scope,receive,send)
            if any(t in path for t in ('transcribe','capture','dictation','call','speech')):
                await send({'type':'websocket.close','code':1013,'reason':'GPU coordination for this live stream is not enabled'})
                return
        if passive or not mutating:return await self.app(scope,receive,send)
        try:require_consumer(Request(scope))
        except HTTPException as exc:
            return await JSONResponse({'detail':exc.detail},status_code=exc.status_code)(scope,receive,send)
        lease=gpu_client.Lease('voicestudio:'+path+':'+uuid.uuid4().hex[:8])
        try:
            lease.value=await gpu_client.acquire_async(lease.owner,lease.kind)
            token=gpu_client.current_lease.set(lease.value)
        except Exception as exc:
            return await JSONResponse({'detail':str(exc)},status_code=503)(scope,receive,send)
        try:return await self.app(scope,receive,send)
        finally:
            try:await asyncio.to_thread(gpu_client.rpc,'release',token=lease.value['token'],timeout=150)
            finally:gpu_client.current_lease.reset(token)

app.add_middleware(Admission)
router=APIRouter(prefix='/api/avatar')

def internal_lease(request):
    value=request.headers.get('x-avatar-lease','')
    try:gpu_client.rpc('validate',token=value,timeout=5)
    except Exception:raise HTTPException(403,'Valid internal GPU lease required')
    return {'token':value}

@router.post('/internal/release')
async def release_models(request:Request):
    try:return await _release_models(request)
    except Exception as exc:
        print('Avatar release failed:',type(exc).__name__,str(exc),flush=True)
        raise

async def _release_models(request):
    lease=internal_lease(request);token=gpu_client.current_lease.set(lease)
    try:
        # stats retain abandoned running jobs even if their HTTP waiter ended.
        deadline=time.monotonic()+90
        while True:
            pool=mm._get_gpu_pool().stats()
            if not pool['running'] and not pool['queued']:break
            if time.monotonic()>deadline:return JSONResponse({'released':False,'error':'GPU workers remain busy'},status_code=409)
            await asyncio.sleep(.25)
        from services import model_lifecycle
        result=await model_lifecycle.unload_all()
        snapshot=model_lifecycle.list_loaded()
        if snapshot.get('degraded_sources') or snapshot.get('models'):
            return JSONResponse({'released':False,'error':'Models could not be completely unloaded','remaining':snapshot.get('models',[])},status_code=409)
        import subprocess
        def memory_free():
            r=subprocess.run([smi_path(),'--query-gpu=memory.free','--format=csv,noheader,nounits'],capture_output=True,text=True,check=True,timeout=8)
            return int(r.stdout.splitlines()[0].strip())
        free=await asyncio.to_thread(memory_free)
        if free<int(os.environ.get('AVATAR_STUDENT_MIN_FREE_MIB','13000')):
            return JSONResponse({'released':False,'error':f'GPU memory still occupied; only {free} MiB free'},status_code=409)
        return {'released':True,'results':result.get('results',{})}
    finally:gpu_client.current_lease.reset(token)

def inside(p,root):
    try:return Path(p).resolve().is_relative_to(root.resolve())
    except (ValueError,OSError):return False

@router.post('/internal/speech')
async def internal_speech(request:Request):
    lease=internal_lease(request);body=await request.json();reqpath=Path(body.get('request',''))
    if not inside(reqpath,ROOT/'results/webui') or reqpath.name!='tts-request.json' or not reqpath.is_file():raise HTTPException(400,'Invalid speech request file')
    params=json.loads(reqpath.read_text());output=Path(params['output'])
    if output.parent.resolve()!=reqpath.parent.resolve() or output.name!='speech.wav':raise HTTPException(400,'Invalid audio output')
    reference=Path(params['reference']) if params.get('reference') else None
    if reference and not any(inside(reference,r) for r in (ASSETS,ROOT/'results/webui',data_dir()/'voices')):raise HTTPException(400,'Invalid voice reference')
    engine=params.get('engine','indextts2')
    from services.tts_backend import get_backend_class
    try:get_backend_class(engine)
    except ValueError:raise HTTPException(422,'Unknown VoiceStudio TTS engine')
    emotion=params.get('emotion')
    if emotion and engine!='indextts2':raise HTTPException(422,'Emotion audio requires IndexTTS')
    if emotion and not any(inside(emotion,r) for r in (ASSETS,ROOT/'results/webui',data_dir()/'voices')):raise HTTPException(400,'Invalid emotion reference')
    token=gpu_client.current_lease.set(lease)
    try:
        if params.get('native_form') is not None:
            return await native_speech(params, reference, emotion, output)
        from services.tts_backend import get_engine_instance_for, engine_in_use
        from services import gpu_gateway
        from services.watermark import mark_synthetic
        backend=get_engine_instance_for(engine)
        from services.tts_backend import ensure_engine_licence
        ensure_engine_licence(type(backend))
        kwargs={'ref_audio':str(reference),'language':'zh','seed':42,'speed':1.0}
        if params.get('ref_text'):kwargs['ref_text']=params['ref_text']
        if emotion:kwargs.update(emo_audio=str(emotion),emo_alpha=params.get('emotion_weight',1.0))
        def synth():
            import soundfile as sf
            wave=backend.generate(params['text'],**kwargs)
            sr=backend.sample_rate
            wave=mark_synthetic(wave,sr,context='avatar.speech')
            if hasattr(wave,'detach'):wave=wave.detach().cpu().numpy()
            wave=__import__('numpy').asarray(wave).squeeze()
            if wave.ndim!=1 or not len(wave):raise ValueError('Speech engine returned empty or non-mono audio')
            temp=output.with_suffix('.wav.part');sf.write(str(temp),wave,sr,format='WAV',subtype='PCM_16')
            info=sf.info(str(temp))
            if info.duration<=0:raise ValueError('Invalid speech output')
            temp.replace(output)
            return {'saved':True,'engine':engine,'sample_rate':sr,'duration':info.duration}
        with engine_in_use(backend):
            await gpu_gateway.prewarm(backend=backend,engine=engine)
            return await mm.run_on_gpu_pool_guarded(synth,what='Avatar speech',timeout=3600)
    finally:gpu_client.current_lease.reset(token)


async def native_speech(params, reference, emotion, output):
    """Use the original /generate validation, conditioning, mastering and history.

    The private ASGI app has no GPU admission middleware: its caller already
    owns the queue's lease. Re-entering the public HTTP app would deadlock.
    """
    from api.routers.generation import router as generation_router
    from avatar_conditioning import emotion_audio
    import contextlib
    speech_app=FastAPI()
    speech_app.include_router(generation_router)
    data=dict(params['native_form'])
    data.update(text=params['text'],engine=params['engine'],stream='false')
    data.pop('profile_id',None)  # the queue owns an immutable reference snapshot
    marker=emotion_audio.set((str(emotion),params.get('emotion_weight',1.0)) if emotion else None)
    try:
        with contextlib.ExitStack() as stack:
            files={'ref_audio':(reference.name,stack.enter_context(reference.open('rb')),'application/octet-stream')} if reference else None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=speech_app),base_url='http://internal',timeout=3600) as client:
                response=await client.post('/generate',data=data,files=files)
        if response.status_code!=200:
            raise HTTPException(response.status_code,response.text[:6000])
        temp=output.with_suffix('.wav.part');temp.write_bytes(response.content)
        import soundfile as sf
        info=sf.info(str(temp))
        if info.duration<=0:raise ValueError('Native speech returned empty audio')
        temp.replace(output)
        # Keep the original profile association in native history; conditioning
        # came from the submitted snapshot, so later edits cannot change a job.
        audio_id=response.headers.get('x-audio-id')
        profile=params.get('voice_profile')
        if profile and audio_id:
            from core.db import db_conn
            with db_conn() as db:
                if db.execute('SELECT 1 FROM voice_profiles WHERE id=?',(profile,)).fetchone():
                    db.execute('UPDATE generation_history SET profile_id=? WHERE id=?',(profile,audio_id));db.commit()
        return {'saved':True,'engine':params['engine'],'sample_rate':info.samplerate,'duration':info.duration,'history_id':audio_id}
    finally:emotion_audio.reset(marker)

public=APIRouter(dependencies=[Depends(require_consumer)])
from character_library import character_router, get_library
# Register named characters before the legacy catch-all proxy.
app.include_router(character_router(require_consumer))
@public.get('/capabilities')
async def capabilities():
    from services.tts_backend import list_backends
    return {'preconfigured':video_backend.list_backends()[0]['available'],'engines':list_backends(),'fps':video_backend.list_backends()[0]['output_fps'],'upload_limit':2*1024**3}

@public.get('/gpu')
async def gpu_status():return await asyncio.to_thread(gpu_client.rpc,'status')

@public.get('/voices')
async def voices():
    from core.db import db_conn
    from core.config import VOICES_DIR
    result=[]
    with db_conn() as db:
        for row in db.execute('SELECT id,name,ref_audio_path,locked_audio_path,ref_text FROM voice_profiles'):
            name=row['locked_audio_path'] or row['ref_audio_path']
            if name and inside(Path(VOICES_DIR)/name,Path(VOICES_DIR)) and (Path(VOICES_DIR)/name).is_file():
                result.append({'id':row['id'],'name':row['name'],'audio_url':'/profiles/'+row['id']+'/audio'})
    return result

def snapshot_voice(profile):
    from core.db import db_conn
    from core.config import VOICES_DIR
    with db_conn() as db:row=db.execute('SELECT * FROM voice_profiles WHERE id=?',(profile,)).fetchone()
    if not row:raise HTTPException(400,'Voice profile not found')
    f=Path(VOICES_DIR)/(row['locked_audio_path'] or row['ref_audio_path'] or '')
    if not inside(f,Path(VOICES_DIR)) or not f.is_file():raise HTTPException(400,'Voice reference file missing')
    digest=hashlib.sha256(f.read_bytes()).hexdigest();relative='音色参考/VoiceStudio/'+digest+f.suffix
    dest=ASSETS/relative;dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():shutil.copy2(f,dest)
    return relative,row['ref_text'] or ''

@public.api_route('/{path:path}',methods=['GET','POST','HEAD'])
async def proxy(path:str,request:Request):
    global ROOT
    ROOT=video_backend.root()
    if not (path in ('files','media','upload','jobs') or path.startswith('jobs/')):raise HTTPException(404,'Unknown avatar endpoint')
    try:await asyncio.to_thread(ensure_queue)
    except RuntimeError as exc:raise HTTPException(503,str(exc))
    target='/api/'+path
    headers={k:v for k,v in request.headers.items() if k.lower() in ('content-type','content-length','range')}
    content=request.stream()
    if path=='jobs' and request.method=='POST':
        data=await request.json()
        if data.get('character_id') and not data.get('audio_only'):
            # Existing request ids keep their original version, even if the
            # character has since been renamed, replaced or soft-deleted.
            selected=None
            if data.get('request_id'):
                async with httpx.AsyncClient(timeout=15,trust_env=False) as lookup:
                    previous=await lookup.get(QUEUE+'/api/jobs',params={'request_id':data['request_id']})
                previous.raise_for_status()
                for job in previous.json()['jobs']:
                    if job.get('request_id')==data['request_id'] and job.get('character_id')==data['character_id']:
                        selected={k:job[k] for k in ('video','character_id','character_name','character_revision')};break
            data.update(selected or await asyncio.to_thread(get_library().selection,data['character_id']))
        if data.get('mode')=='tts' and data.get('native_form') is not None:
            prepare_native_job(data)
        elif data.get('voice_profile'):
            data['reference'],data['ref_text']=snapshot_voice(data.pop('voice_profile'))
        if data.get('emotion_enabled') is not True:data['emotion']=''
        if data.get('mode')=='tts':data.setdefault('tts_provider','voicestudio');data.setdefault('tts_engine','indextts2')
        content=json.dumps(data,ensure_ascii=False).encode();headers.pop('content-length',None)
        headers['content-type']='application/json'
    client=httpx.AsyncClient(timeout=httpx.Timeout(120,read=21600),trust_env=False)
    try:
        upstream=await client.send(client.build_request(request.method,QUEUE+target+('?' + request.url.query if request.url.query else ''),headers=headers,content=content),stream=True)
    except Exception:
        await client.aclose();raise
    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():yield chunk
        finally:await upstream.aclose();await client.aclose()
    response_headers={k:v for k,v in upstream.headers.items() if k.lower() in ('content-type','content-length','content-range','accept-ranges','content-disposition','cache-control')}
    return StreamingResponse(stream(),status_code=upstream.status_code,headers=response_headers)

router.include_router(public)
app.include_router(router)


NATIVE_FIELDS=frozenset(('language','ref_text','instruct','duration','num_step','guidance_scale','speed','t_shift','denoise','postprocess_output','layer_penalty_factor','position_temperature','class_temperature','emo_text','emo_alpha','emo_vector','seed','effect_preset','wav_bits','preset_voice','max_chunk_chars','crossfade_ms','pronounce'))

def prepare_native_job(data):
    from services.tts_backend import active_backend_id, get_backend_class, list_backends
    form=data['native_form']
    if not isinstance(form,dict) or set(form)-NATIVE_FIELDS or any(not isinstance(v,str) or len(v)>10000 for v in form.values()):
        raise HTTPException(422,'Invalid native VoiceStudio form')
    engine=data.get('tts_engine') or active_backend_id()
    try:cls=get_backend_class(engine)
    except ValueError:raise HTTPException(422,'Unknown VoiceStudio TTS engine')
    if not any(b['id']==engine and b.get('available') for b in list_backends()):raise HTTPException(422,'TTS engine is not installed; configure it in VoiceStudio Models')
    data['tts_engine']=engine
    if getattr(cls,'supports_cloning',True) is False:
        data['reference']=None;data.pop('voice_profile',None)
    elif data.get('voice_profile'):
        from core.db import db_conn
        from api.routers.generation import _resolve_profile_conditioning
        with db_conn() as db:row=db.execute('SELECT * FROM voice_profiles WHERE id=?',(data['voice_profile'],)).fetchone()
        if not row or row['kind']!='clone':raise HTTPException(422,'Choose a saved cloning voice')
        cond=_resolve_profile_conditioning(row,ref_text=form.get('ref_text'),instruct=form.get('instruct'),seed=int(form['seed']) if form.get('seed') else None,language=form.get('language'),engine_id=engine)
        data['reference'],_=snapshot_voice(data['voice_profile'])
        for key in ('ref_text','instruct','seed','language'):
            if cond.get(key) is not None:form[key]=str(cond[key])
    elif not data.get('reference'):raise HTTPException(422,'Choose a voice or upload a voice recording')
    data['native_form']=dict(form)
