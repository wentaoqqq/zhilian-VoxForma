import importlib.util,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]/'services'
spec=importlib.util.spec_from_file_location('managed_video_backend',HERE/'video_backend.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

@unittest.skipUnless(os.name == 'posix', 'Requires sparse Linux filesystem fixtures')
class ManagedVideoTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'engine';self.runtime=Path(self.temp.name)/'runtime';self.python=self.runtime/'bin/python'
  self.patch=patch.dict(os.environ,{'OMNIVOICE_TBDUB_DIR':str(self.root),'OMNIVOICE_TBDUB_PYTHON':str(self.python)});self.patch.start()
  for f in ('inference.py','webui/unified_jobs.py','runtime_config.py'):self.make(self.root/f,1)
  from services.video_install import manifest
  for item in manifest()['files']:
   if item['role']!='rife':self.make(self.root/'checkpoints'/item['path'],item['size'])
  self.make(self.python,1);self.make(self.runtime/'voicestudio-runtime-verified.json',1)
 def make(self,p,n):
  p.parent.mkdir(parents=True,exist_ok=True)
  with p.open('wb') as f:f.truncate(n)
 def tearDown(self):self.patch.stop();self.temp.cleanup()
 def test_missing_interpolator_preserves_native_generation(self):
  state=mod.list_backends()[0];self.assertTrue(state['available']);self.assertEqual(state['output_fps'],[25])
  self.assertFalse(any(m['required'] for m in mod.model_rows()),'Optional video installation must never block native TTS setup')
 def test_incomplete_student_is_reported_unavailable(self):
  (self.root/'checkpoints/tbdub_student.safetensors').write_bytes(b'partial')
  self.assertFalse(mod.list_backends()[0]['available']);self.assertIn('TBDub Student',mod.list_backends()[0]['missing_models'])
 def test_interpreter_without_verified_environment_is_not_ready(self):
  (self.runtime/'voicestudio-runtime-verified.json').unlink();state=mod.list_backends()[0]
  self.assertFalse(state['runtime_installed']);self.assertFalse(state['available'])

if __name__=='__main__':unittest.main()
