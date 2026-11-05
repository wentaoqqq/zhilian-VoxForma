"""Lightweight persistent queue. Model runtimes live only in child processes."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import shutil
import hashlib
from gpu_runtime import execution_lease, delegate_env, check_gpu_for_video

class QueueFullError(ValueError):
    pass

class JobManager:
    capacity = 16
    def __init__(self, root: Path, assets: Path, python: Path):
        self.root, self.assets, self.python = root, assets, python
        self.out = root/'results'/'webui'
        self.tts = Path(os.environ.get('OMNIVOICE_INDEXTTS_DIR', str(Path.home()/'.omnivoice/engines/indextts2')))
        self.jobs = {}
        self.lock = threading.RLock()
        self.pending = queue.Queue()
        self.started = False
        self.active = None

    def save(self, jid, **changes):
        with self.lock:
            self.jobs[jid].update(changes, updated=time.time())
            folder=self.out/jid; folder.mkdir(parents=True,exist_ok=True)
            temp=folder/'job.json.tmp'
            temp.write_text(json.dumps(self.jobs[jid], ensure_ascii=False, indent=2))
            temp.replace(folder/'job.json')

    def get(self, jid):
        with self.lock:
            return dict(self.jobs.get(jid,{}))

    def start(self):
        with self.lock:
            if self.started: return
            self.out.mkdir(parents=True,exist_ok=True)
            for state in self.out.glob('student_*/job.json'):
                try:
                    j=json.loads(state.read_text()); jid=j['id']
                    self.jobs[jid]=j
                    if j['status'] in ('queued','running'):
                        self.save(jid,status='failed',message='服务重启，任务已中断；已保存的配音仍可下载或复用。',error='interrupted by service restart')
                except (OSError, ValueError, KeyError): pass
            self.started=True
            threading.Thread(target=self.worker,daemon=True,name='unified-queue').start()

    def submit(self, payload):
        self.start()
        with self.lock:
            request_id=payload.get('request_id')
            if request_id:
                for previous in self.jobs.values():
                    if previous.get('request_id')==request_id:
                        if previous.get('request_fingerprint')!=payload.get('request_fingerprint'):
                            raise ValueError('request_id already belongs to another payload')
                        return self.get(previous['id'])
            if sum(j['status'] in ('queued','running') for j in self.jobs.values()) >= self.capacity:
                raise QueueFullError('队列已满，请等任务完成后再提交，本条尚未入队。')
            jid=time.strftime('student_%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
            self.jobs[jid]=dict(payload,id=jid,status='queued',message='排队中…',created=time.time())
            folder=self.out/jid;folder.mkdir(parents=True,exist_ok=True)
            for key in ('reference','emotion','audio'):
                if payload.get(key):
                    source=self.assets/payload[key]
                    dest=folder/(key+source.suffix)
                    shutil.copy2(source,dest)
                    self.jobs[jid][key+'_snapshot']=str(dest)
            self.save(jid)
            self.pending.put(jid)
            return self.get(jid)

    def command(self,jid,cmd,cwd,log,timeout,status_file=None):
        """Wait for process exit (CUDA context gone), never just an HTTP result."""
        proc=subprocess.Popen([str(x) for x in cmd],cwd=cwd,stdout=log,stderr=subprocess.STDOUT,
                              start_new_session=True,env={**os.environ,**delegate_env(),'PYTHONUNBUFFERED':'1'})
        self.save(jid,pid=proc.pid)
        start=time.monotonic(); last_stage=None
        try:
            while proc.poll() is None:
                if time.monotonic()-start>timeout: raise TimeoutError('本阶段运行超时。')
                if status_file and status_file.exists():
                    try:
                        stage=json.loads(status_file.read_text())['stage']
                        if stage!=last_stage:
                            message={'loading':'正在加载 IndexTTS2…','synthesizing':'正在生成配音…',
                                     'saved':'配音已保存，正在等待语音进程退出、释放资源…'}.get(stage,stage)
                            self.save(jid,stage='tts_'+stage,message=message)
                            last_stage=stage
                    except (ValueError,KeyError,OSError): pass
                time.sleep(0.5)
            return proc.returncode
        finally:
            # On failure/timeout, do not leave an orphan model using GPU memory.
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL); proc.wait()
            self.save(jid,pid=None)

    def worker(self):
        while True:
            jid=self.pending.get()
            try:
                self.active=jid
                self.execute(jid)
            except Exception as exc:
                path=self.out/jid/'run.log'
                tail=''
                if path.exists():
                    with path.open('rb') as f:
                        f.seek(0,2); f.seek(max(0,f.tell()-6000)); tail=f.read().decode(errors='replace')
                self.save(jid,status='failed',message='任务失败；若配音已生成，可下载并直接重试视频。',error=f'{exc}\n{tail}')
            finally:
                self.active=None; self.pending.task_done()

    def execute(self,jid):
        job=self.get(jid); folder=self.out/jid
        with execution_lease(self.root,jid) as lease:
            self.save(jid,status='running',stage='waiting_resource',message='等待推理资源…')
            with (folder/'run.log').open('a',buffering=1) as log:
                log.write(f'JOB_START={time.time()}\n')
                if job['mode']=='tts':
                    audio=folder/'speech.wav'; status=folder/'tts-status.json'
                    request={'text':job['text'],'reference':job.get('reference_snapshot') or (str(self.assets/job['reference']) if job.get('reference') else None),
                             'engine':job.get('tts_engine','indextts2'),'ref_text':job.get('ref_text',''),
                             'emotion':job.get('emotion_snapshot',str(self.assets/job['emotion'])) if job.get('emotion') else None,
                             'emotion_weight':job.get('emotion_weight',1.0),'output':str(audio),
                             'status_file':str(status)}
                    if 'native_form' in job:
                        request.update(native_form=job['native_form'],voice_profile=job.get('voice_profile'))
                    req=folder/'tts-request.json'; req.write_text(json.dumps(request,ensure_ascii=False))
                    self.save(jid,stage='tts_loading',message='正在加载 '+job.get('tts_engine','IndexTTS2')+'…')
                    t0=time.monotonic()
                    if job.get('tts_provider')=='voicestudio':
                        worker=Path(os.environ['AVATAR_INTEGRATION_DIR'])/'voice_worker.py'
                        rc=self.command(jid,[self.python,worker,'--request',req],self.root,log,3600,status)
                    else:
                        rc=self.command(jid,[self.tts/'.venv/bin/python',self.tts/'tts_worker.py','--request',req],self.tts,log,3600,status)
                    log.write(f'TTS_EXIT={rc}\nTTS_SECONDS={time.monotonic()-t0:.3f}\n')
                    if rc or not audio.is_file(): raise RuntimeError(f'配音生成失败，退出码 {rc}')
                    check=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(audio)],capture_output=True,text=True,timeout=15,check=True)
                    duration=float(json.loads(check.stdout)['format']['duration'])
                    if duration<=0: raise RuntimeError('配音为空。')
                    self.save(jid,audio_output=str(audio),audio_duration=duration,stage='tts_releasing')
                    lease.clear_voice()
                    self.save(jid,audio_output=str(audio),audio_duration=duration,tts_released=True,
                              stage='tts_released',message='配音已保存，语音进程已退出。')
                else:
                    audio=Path(job['saved_audio']) if job.get('saved_audio') else Path(job.get('audio_snapshot',str(self.assets/job['audio'])))
                    self.save(jid,audio_output=str(audio))
                if job.get('audio_only'):
                    self.save(jid,status='succeeded',stage='complete',message='配音完成，模型进程已退出。')
                    log.write('RUN_EXIT=0\n'); return
                video=self.assets/job['video']; fps=job['fps']; output=folder/f'{jid}_tbdub.mp4'
                with tempfile.TemporaryDirectory(prefix='timebase_',dir=folder) as work:
                    prepared=Path(work)/'source_25fps.mkv'
                    self.save(jid,stage='preparing',message='正在按配音时长整理视频时间轴…')
                    rc=self.command(jid,[self.python,self.root/'webui/prepare_video.py','--video',video,'--audio',audio,'--output',prepared],self.root,log,3600)
                    if rc: raise RuntimeError(f'视频时间轴预处理失败，退出码 {rc}')
                    check_gpu_for_video()
                    self.save(jid,stage='video',message='正在生成数字人视频…')
                    t0=time.monotonic()
                    cmd=[self.python,self.root/'inference.py','--video',prepared,'--audio',audio,'--sample-name',jid,
                         '--inference-mode','student','--num-student-steps','2','--sigma-shift','1.0',
                         '--motion-from-latents','--cpu-offload','--stream-vae','--seed','42','--output-dir',folder]
                    rc=self.command(jid,cmd,self.root,log,21600)
                    log.write(f'VIDEO_EXIT={rc}\nVIDEO_SECONDS={time.monotonic()-t0:.3f}\n')
                    if rc or not output.is_file(): raise RuntimeError(f'数字人生成失败，退出码 {rc}')
                    if fps==60:
                        self.save(jid,stage='interpolating',message='正在插帧到 60 fps…')
                        rc=self.command(jid,[self.python,self.root/'webui/interpolate_25_to_30.py','--input',output,'--output',output,'--fps','60'],self.root,log,21600)
                        if rc: raise RuntimeError(f'插帧失败，退出码 {rc}')
                log.write('RUN_EXIT=0\n')
                self.save(jid,status='succeeded',stage='complete',message=f'生成完成（{fps} fps）。',output=str(output),size=f'{output.stat().st_size/1024**2:.1f} MB')
