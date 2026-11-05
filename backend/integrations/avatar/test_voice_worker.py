"""The speech subprocess must contact the configured launcher port."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

import avatar_paths


class VoiceWorkerTests(unittest.TestCase):
    def test_legacy_service_port_is_the_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(urlsplit(avatar_paths.voice_url()).port, 3901)

    def test_worker_uses_configured_port_and_keeps_lease_header(self):
        received = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.update(path=self.path, lease=self.headers.get('X-Avatar-Lease'))
                received['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                body = b'{"saved":true}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as folder:
            request = Path(folder)/'request.json'
            status = Path(folder)/'status.json'
            request.write_text(json.dumps({'status_file':str(status)}))
            env = dict(os.environ, AVATAR_VOICE_URL=f'http://127.0.0.1:{server.server_port}/',
                       AVATAR_GPU_DELEGATE='test-delegated-lease')
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('voice_worker.py')),
                                     '--request', str(request)], env=env, capture_output=True, text=True,errors='replace', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(received['path'], '/api/avatar/internal/speech')
            self.assertEqual(received['lease'], 'test-delegated-lease')
            self.assertEqual(received['body']['request'], str(request))
            self.assertEqual(json.loads(status.read_text())['stage'], 'saved')

if __name__ == '__main__':
    unittest.main()
