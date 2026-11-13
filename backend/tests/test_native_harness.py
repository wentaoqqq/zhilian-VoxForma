import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from production.harness_proxy import NativeHarnessProxy


@pytest.fixture
def client(tmp_path, monkeypatch):
    requests = []
    class Native(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append((self.path, self.headers.get('Host'), self.headers.get('Cookie')))
            self.send_response(200)
            self.send_header('Set-Cookie', 'native=session; Path=/; HttpOnly; SameSite=Lax')
            self.end_headers()
            self.wfile.write(b'native-ui')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Native)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    entry = tmp_path / 'entry.json'
    entry.write_text(json.dumps({'url': 'http://local/native-harness/?token=fixture'}))
    monkeypatch.setenv('VOXFORMA_HARNESS_ENTRY_FILE', str(entry))
    monkeypatch.setenv('VOXFORMA_HARNESS_UPSTREAM', f'http://127.0.0.1:{server.server_port}')
    app = FastAPI()
    @app.get('/unrelated')
    def unrelated():
        return {'unchanged': True}
    app.add_middleware(NativeHarnessProxy)
    try:
        with TestClient(app) as client:
            yield client, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_native_entry_cookie_and_origin_pass_through(client):
    client, requests = client
    response = client.get('/native-harness/?token=fixture')
    assert response.content == b'native-ui'
    assert requests[-1][:2] == ('/?token=fixture', 'testserver')
    assert 'Path=/native-harness/' in response.headers['set-cookie']
    client.get('/native-harness/api/session?x=1')
    assert requests[-1][0] == '/api/session?x=1'
    assert requests[-1][2] == 'native=session'
    assert client.get('/unrelated').json() == {'unchanged': True}


def test_native_auth_is_not_added_by_proxy(client):
    client, requests = client
    client.get('/native-harness/api/session')
    assert requests[-1][2] is None
