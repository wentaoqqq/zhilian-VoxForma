"""Installer failure modes, real HTTP resume, and offline status checks."""
import hashlib
import json
import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from services import video_install as vi, video_backend as vb


@unittest.skipIf(sys.platform != 'linux', 'The Student installer supports Linux/WSL only')
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(vi, 'DATA_DIR', str(self.root/'data'))
        self.patch.start(); self.addCleanup(self.patch.stop)
        vi._cancel.clear()

    def spec(self, content=b'checkpoint-data'):
        return dict(path='weight.bin', role='student', size=len(content),
                    sha256=hashlib.sha256(content).hexdigest())

    def test_same_size_corruption_is_rejected(self):
        p=self.root/'model';p.write_bytes(b'wrong-data-here!')
        s=self.spec(b'right-data-here!')
        self.assertTrue(vi.valid(p,s,checksum=False))
        self.assertFalse(vi.valid(p,s))

    def test_real_http_download_resumes_partial_file(self):
        content=b'abcxyz'*10000
        ranges=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                ranges.append(self.headers.get('Range'))
                start=int(self.headers.get('Range','bytes=0-')[6:].split('-')[0])
                self.send_response(206 if start else 200)
                self.send_header('Content-Length',str(len(content)-start))
                if start:self.send_header('Content-Range',f'bytes {start}-{len(content)-1}/{len(content)}')
                self.end_headers();self.wfile.write(content[start:])
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        s=dict(self.spec(content),url=f'http://127.0.0.1:{server.server_port}/weight')
        dest=self.root/'candidate/checkpoints/weight.bin';dest.parent.mkdir(parents=True)
        dest.with_suffix('.bin.partial').write_bytes(content[:1234])
        with patch.object(vb,'root',return_value=self.root/'old'):
            result=vi.Install(self.root/'candidate').download(s)
        self.assertEqual(result.read_bytes(),content)
        self.assertEqual(ranges,['bytes=1234-'])
        self.assertFalse(dest.with_suffix('.bin.partial').exists())

    def test_cancel_preserves_partial_and_previous_activation(self):
        state=vi.state_dir();vi.atomic_json(state/'active.json',{'root':'previous','recipe':'old'})
        partial=self.root/'partial';partial.write_bytes(b'keep')
        vi._cancel.set()
        with self.assertRaises(vi.Cancelled):vi.Install(self.root/'candidate').check()
        self.assertEqual(partial.read_bytes(),b'keep')
        self.assertEqual(json.loads((state/'active.json').read_text())['root'],'previous')

    def test_status_interrupt_is_visible_and_does_not_download(self):
        vi.atomic_json(vi.state_dir()/'job.json',{'state':'running','step':'models'})
        with patch.object(vi,'_thread',None), patch.object(vb,'list_backends',return_value=[{'available':False}]), patch('httpx.stream',side_effect=AssertionError('network')):
            self.assertEqual(vi.status()['job']['state'],'interrupted')

    def test_another_process_install_is_not_marked_interrupted(self):
        import fcntl
        vi.atomic_json(vi.state_dir()/'job.json',{'state':'running','step':'models'})
        with (vi.state_dir()/'install.lock').open('a') as f:
            fcntl.flock(f,fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(vi,'_thread',None), patch.object(vb,'list_backends',return_value=[{'available':False}]):
                self.assertEqual(vi.status()['job']['state'],'running')

    def test_source_is_portable_and_has_no_user_data(self):
        names=[str(p.relative_to(vi.VENDOR)) for p in vi.VENDOR.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
        self.assertFalse(any(any(x in n for x in ('checkpoints/','results/','backups/','.bak')) for n in names if n != 'checkpoints/README.md'))
        for rel in ['webui/app.py','webui/unified_jobs.py']:
            text=(vi.VENDOR/rel).read_text()
            self.assertNotIn('/home/dministrator',text)
            self.assertNotIn('/mnt/d/szr',text)
        self.assertTrue((vi.VENDOR/'LICENSE').exists())
        self.assertTrue((vi.VENDOR/'webui/rife_v425/LICENSE').exists())

    def test_manifest_paths_and_revisions_are_pinned(self):
        for spec in vi.manifest()['files']:
            self.assertFalse(Path(spec['path']).is_absolute())
            self.assertNotIn('..',Path(spec['path']).parts)
            self.assertEqual(len(spec['sha256']),64)
            if 'repo' in spec:self.assertRegex(spec['revision'],r'^[a-f0-9]{40}$')

    def test_failed_probe_does_not_activate_or_mark_runtime_ready(self):
        old={'root':'previous','recipe':'old'}
        vi.atomic_json(vi.state_dir()/'active.json',old)
        target=self.root/'candidate'
        def command(task, argv, **kwargs):
            if 'venv' in argv:
                p=target/'.venv/bin/python';p.parent.mkdir(parents=True);p.touch()
            if '-c' in argv:raise RuntimeError('probe failed')
        with patch.object(vi,'supported',return_value=(True,'')), patch('services.sidecar_install._locate_uv',return_value='/fake/uv'), patch('services.media_tools.status',return_value={'ready':True}), patch('services.media_tools._resolve',return_value='/usr/bin/true'), patch.object(vi,'manifest',return_value={'files':[]}), patch.object(vi.Install,'run_command',command):
            with self.assertRaisesRegex(RuntimeError,'probe failed'):
                vi.Install(target).run()
        self.assertEqual(json.loads((vi.state_dir()/'active.json').read_text()),old)
        self.assertFalse((target/'.tbdub-install.json').exists())
        self.assertEqual(json.loads((vi.state_dir()/'job.json').read_text())['state'],'failed')

    def test_no_install_on_unsupported_host(self):
        with patch.object(vi,'supported',return_value=(False,'unsupported')), patch('threading.Thread') as thread:
            with self.assertRaisesRegex(ValueError,'unsupported'):vi.start_install()
            thread.assert_not_called()

    def test_redacts_tokens_and_signed_urls(self):
        secret='hf_'+'a'*30
        text=vi._redact('https://user:pass@host/file?X-Amz-Signature=abc&token=def '+secret)
        for part in ('user:pass','Signature=abc','token=def',secret):self.assertNotIn(part,text)

if __name__=='__main__':unittest.main()
