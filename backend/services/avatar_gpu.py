"""Optional GPU coordination, enabled only by the digital-human entrypoint."""
import asyncio
import functools
import uuid

_client = None


def enable(client):
    global _client
    _client = client


async def acquire(owner):
    client = _client
    if client is None:
        return None
    value = await client.acquire_async(owner)
    token = client.current_lease.set(value)
    return client, value, token


async def release(handle):
    if handle is None:
        return
    client, value, token = handle
    try:
        await asyncio.shield(asyncio.to_thread(client.rpc, 'release', token=value['token'], timeout=150))
    finally:
        client.current_lease.reset(token)


def async_job(fn):
    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        handle = await acquire('voicestudio:background:' + uuid.uuid4().hex[:10])
        try:
            return await fn(*args, **kwargs)
        finally:
            await release(handle)
    return wrapped
