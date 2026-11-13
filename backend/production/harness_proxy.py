"""Thin same-origin transport for the unmodified DeepSeek Harness Web UI."""
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse

PREFIX = '/native-harness'
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length'}


def entry_url():
    path = os.environ.get('VOXFORMA_HARNESS_ENTRY_FILE', '')
    if not path or not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding='utf-8')).get('url')


class NativeHarnessProxy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] not in {'http', 'websocket'} or not (path == PREFIX or path.startswith(PREFIX + '/')):
            return await self.app(scope, receive, send)
        upstream = os.environ.get('VOXFORMA_HARNESS_UPSTREAM', 'http://127.0.0.1:3080').rstrip('/')
        rest = path[len(PREFIX):] or '/'
        headers = [(k.decode('latin1'), v.decode('latin1')) for k, v in scope['headers'] if k.decode('latin1').lower() not in HOP]
        target = upstream + rest
        if scope.get('query_string'):
            target += '?' + scope['query_string'].decode('ascii')
        if scope['type'] == 'websocket':
            return await self.websocket(scope, receive, send, target, headers)
        if path == PREFIX:
            return await RedirectResponse(PREFIX + '/')(scope, receive, send)
        client = httpx.AsyncClient(trust_env=False, timeout=None)
        try:
            request = Request(scope, receive)
            response = await client.send(client.build_request(request.method, target, headers=headers, content=request.stream()), stream=True)
        except httpx.RequestError:
            await client.aclose()
            return await JSONResponse({'detail': 'Editing assistant is unavailable.'}, status_code=503)(scope, receive, send)
        async def body():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        result = StreamingResponse(body(), status_code=response.status_code)
        result.raw_headers = []
        for key, value in response.headers.multi_items():
            if key.lower() in HOP:
                continue
            if key.lower() == 'set-cookie':
                value = value.replace('Path=/', 'Path=' + PREFIX + '/')
            result.raw_headers.append((key.encode('latin1'), value.encode('latin1')))
        await result(scope, receive, send)

    async def websocket(self, scope, receive, send, target, headers):
        from starlette.websockets import WebSocket, WebSocketDisconnect
        from websockets.asyncio.client import connect
        from websockets.exceptions import WebSocketException
        ws = WebSocket(scope, receive, send)
        authority = next((v for k, v in headers if k.lower() == 'host'), urlsplit(target).netloc)
        upstream = urlsplit(target)
        target = urlunsplit(('wss' if upstream.scheme == 'https' else 'ws', authority, upstream.path, upstream.query, ''))
        headers = [(k, v) for k, v in headers if k.lower() != 'host' and not k.lower().startswith('sec-websocket-')]
        try:
            async with connect(target, host=upstream.hostname, port=upstream.port or (443 if upstream.scheme == 'https' else 80), additional_headers=headers,
                               subprotocols=scope.get('subprotocols') or None, max_size=None, proxy=None) as remote:
                await ws.accept(subprotocol=remote.subprotocol)
                async def upload():
                    while True:
                        message = await ws.receive()
                        if message['type'] == 'websocket.disconnect':
                            return
                        await remote.send(message.get('text') if message.get('text') is not None else message['bytes'])
                async def download():
                    async for message in remote:
                        await (ws.send_text(message) if isinstance(message, str) else ws.send_bytes(message))
                tasks = [asyncio.create_task(upload()), asyncio.create_task(download())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, WebSocketDisconnect, WebSocketException):
            pass
        finally:
            with suppress(RuntimeError):
                await ws.close()
