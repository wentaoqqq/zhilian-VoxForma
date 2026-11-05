"""CPU-only regression tests for resource ownership and immutable queue inputs."""
import tempfile,subprocess,os,sys,json,socket,time,threading,unittest,fcntl,importlib.util,shutil
from pathlib import Path
HERE=Path(__file__).parent

class SchedulerTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.socket=str(self.root/'gpu.sock');self.lock=self.root/'run.lock'
  env={**os.environ,'AVATAR_GPU_SOCKET':self.socket,'AVATAR_GPU_LOCK':str(self.lock),'AVATAR_GPU_STATE':str(self.root/'state.json'),'AVATAR_VOICE_URL':'http://127.0.0.1:1'}
  self.proc=subprocess.Popen([sys.executable,str(HERE/'gpu_scheduler.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
  for _ in range(100):
   if Path(self.socket).exists():break
   if self.proc.poll() is not None:raise RuntimeError(self.proc.stderr.read().decode())
   time.sleep(.02)
 def tearDown(self):
  self.proc.terminate();self.proc.wait(timeout=5);self.proc.stderr.close();self.temp.cleanup()
 def call(self,action,**args):
  with socket.socket(socket.AF_UNIX) as s:
   s.settimeout(5);s.connect(self.socket);f=s.makefile('rwb');f.write(json.dumps({'action':action,**args}).encode()+b'\n');f.flush();return json.loads(f.readline())
 def acquire(self,label):return self.call('acquire',owner=label,kind='test',pid=os.getpid(),request_id=label)
 def test_delegation_and_invalid_token(self):
  first=self.acquire('a');self.assertTrue(first['ok'])
  self.assertTrue(self.call('validate',token=first['token'])['ok'])
  self.assertFalse(self.call('validate',token='forged')['ok'])
  self.assertNotIn('token',self.call('status')['owner'])
  self.assertTrue(self.call('release',token=first['token'])['ok'])
  self.assertFalse(self.call('validate',token=first['token'])['ok'])
 def test_fifo_and_physical_legacy_lock(self):
  first=self.acquire('a');received=[]
  def second():
   item=self.acquire('b');received.append(item);self.call('release',token=item['token'])
  thread=threading.Thread(target=second);thread.start();time.sleep(.15)
  self.assertFalse(received)
  with self.lock.open('a') as f:
   with self.assertRaises(BlockingIOError):fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  self.call('release',token=first['token']);thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(received[0]['owner'],'b')
 def test_unconfirmed_release_keeps_exclusive_ownership(self):
  # Registered-but-unreachable backend must never be treated as unloaded.
  (self.root/'voice-service-registered').touch()
  first=self.acquire('a');result=self.call('release',token=first['token']);self.assertFalse(result['ok'])
  status=self.call('status');self.assertEqual(status['owner']['owner'],'a');self.assertTrue(status['blocked'])
  self.assertFalse(self.acquire('b')['ok'])
 def test_dead_owner_is_recovered_before_another_grant(self):
  code="import socket,json; s=socket.socket(socket.AF_UNIX);s.connect(%r);f=s.makefile('rwb');f.write(json.dumps({'action':'acquire','owner':'exiting','request_id':'exit-test'}).encode()+b'\\n');f.flush();assert json.loads(f.readline())['ok']" % self.socket
  subprocess.run([sys.executable,'-c',code],check=True)
  for _ in range(50):
   if not self.call('status')['owner']:break
   time.sleep(.05)
  self.assertFalse(self.call('status')['owner'])
  item=self.acquire('next');self.assertTrue(item['ok']);self.call('release',token=item['token'])
 def test_arbiter_restart_does_not_grant_while_owner_survives(self):
  item=self.acquire('surviving')
  self.proc.terminate();self.proc.wait(timeout=5);self.proc.stderr.close()
  env={**os.environ,'AVATAR_GPU_SOCKET':self.socket,'AVATAR_GPU_LOCK':str(self.lock),'AVATAR_GPU_STATE':str(self.root/'state.json'),'AVATAR_VOICE_URL':'http://127.0.0.1:1'}
  self.proc=subprocess.Popen([sys.executable,str(HERE/'gpu_scheduler.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
  for _ in range(100):
   try:
    if self.call('status').get('blocked'):break
   except (OSError,ConnectionError):pass
   time.sleep(.02)
  self.assertFalse(self.acquire('another')['ok'])
  self.assertTrue(self.call('release',token=item['token'])['ok'])
  item=self.acquire('after-recovery');self.assertTrue(item['ok']);self.call('release',token=item['token'])

class QueueTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name)
  source=HERE/'vendor/tbdub_student/webui/unified_jobs.py'
  shutil.copy2(source,root/'unified_jobs.py')
  spec=importlib.util.spec_from_file_location('queue_test_module',root/'unified_jobs.py');cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def setUp(self):
  self.folder=tempfile.TemporaryDirectory();self.root=Path(self.folder.name);self.assets=self.root/'assets';self.assets.mkdir();(self.assets/'ref.wav').write_bytes(b'original')
  self.manager=self.module.JobManager(self.root,self.assets,Path(sys.executable));self.manager.started=True
 def tearDown(self):self.folder.cleanup()
 def test_voice_snapshot_survives_source_changes(self):
  job=self.manager.submit({'mode':'tts','reference':'ref.wav'});(self.assets/'ref.wav').write_bytes(b'replaced')
  self.assertEqual(Path(job['reference_snapshot']).read_bytes(),b'original')
 def test_duplicate_submit_does_not_create_a_second_job(self):
  payload={'mode':'tts','reference':'ref.wav','request_id':'abcdef123','request_fingerprint':'same'}
  first=self.manager.submit(payload);second=self.manager.submit(payload)
  self.assertEqual(first['id'],second['id']);self.assertEqual(self.manager.pending.qsize(),1)
  with self.assertRaises(ValueError):self.manager.submit({**payload,'request_fingerprint':'different'})
 def test_retry_an_accepted_request_even_when_capacity_is_full(self):
  self.manager.capacity=1
  payload={'mode':'tts','reference':'ref.wav','request_id':'capacity-test','request_fingerprint':'same'}
  first=self.manager.submit(payload)
  self.assertEqual(self.manager.submit(payload)['id'],first['id'])

if __name__=='__main__':unittest.main(verbosity=2)
