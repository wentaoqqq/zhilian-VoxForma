"""Ordinary VoiceStudio imports stay portable; the avatar launcher opts in."""
import contextvars
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import avatar_gpu


class OptionalAvatarGpuTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_coordination_runs_without_a_scheduler(self):
        with patch.object(avatar_gpu, '_client', None):
            @avatar_gpu.async_job
            async def work(value):
                return value + 1
            self.assertEqual(await work(2), 3)
            self.assertIsNone(await avatar_gpu.acquire('plain-websocket'))
            await avatar_gpu.release(None)

    def client(self):
        current = contextvars.ContextVar('test-avatar-lease', default=None)
        events = []
        async def acquire(owner):
            events.append(('acquire', owner))
            return {'token': 'test-lease'}
        def rpc(action, **kwargs):
            events.append((action, kwargs['token']))
        return SimpleNamespace(current_lease=current, acquire_async=acquire, rpc=rpc), events

    async def test_enabled_job_keeps_its_context_and_releases_on_failure(self):
        client, events = self.client()
        with patch.object(avatar_gpu, '_client', None):
            avatar_gpu.enable(client)
            @avatar_gpu.async_job
            async def work():
                self.assertEqual(client.current_lease.get()['token'], 'test-lease')
                raise RuntimeError('generation failed')
            with self.assertRaisesRegex(RuntimeError, 'generation failed'):
                await work()
            self.assertIsNone(client.current_lease.get())
            self.assertEqual(events[-1], ('release', 'test-lease'))

    async def test_websocket_handle_restores_previous_context(self):
        client, events = self.client()
        with patch.object(avatar_gpu, '_client', client):
            previous = {'token': 'previous'}
            client.current_lease.set(previous)
            handle = await avatar_gpu.acquire('websocket-message')
            self.assertEqual(client.current_lease.get(), {'token': 'test-lease'})
            await avatar_gpu.release(handle)
            self.assertIs(client.current_lease.get(), previous)
            self.assertEqual(events, [('acquire', 'websocket-message'), ('release', 'test-lease')])

    async def test_release_failure_still_resets_context(self):
        client, _ = self.client()
        def fail(*args, **kwargs):
            raise RuntimeError('release failed')
        client.rpc = fail
        with patch.object(avatar_gpu, '_client', client):
            handle = await avatar_gpu.acquire('request')
            with self.assertRaisesRegex(RuntimeError, 'release failed'):
                await avatar_gpu.release(handle)
            self.assertIsNone(client.current_lease.get())
