"""Bodyless media requests must not send chunked bodies to the HTTP/1.0 queue."""
import ast,asyncio,json,threading
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import StreamingResponse


def test_media_get_is_bodyless_and_download_is_complete(tmp_path):
    payload=b'RIFF'+bytes(2*1024*1024)
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get('Transfer-Encoding'))
            body=payload if not seen[-1] else b'Unexpected chunked GET'
            self.send_response(200 if not seen[-1] else 400)
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    source=Path(__file__).resolve().parents[1]/'integrations/avatar/avatar_server.py'
    fn=next(n for n in ast.parse(source.read_text(encoding='utf8')).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='proxy');fn.decorator_list=[]
    scope={'Request':Request,'HTTPException':HTTPException,'ROOT':tmp_path,'video_backend':SimpleNamespace(root=lambda:tmp_path),'ensure_queue':lambda:None,'asyncio':asyncio,'httpx':httpx,'json':json,'QUEUE':f'http://127.0.0.1:{server.server_port}','StreamingResponse':StreamingResponse}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),scope)
    app=FastAPI();app.api_route('/{path:path}',methods=['GET'])(scope['proxy'])
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            for _ in range(3):
                response=await client.get('/jobs/test/audio')
                assert response.status_code==200 and response.content==payload
    try:asyncio.run(run());assert seen==[None]*3
    finally:server.shutdown();server.server_close();thread.join()
