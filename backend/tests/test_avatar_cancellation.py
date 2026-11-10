"""Real async/thread cancellation bridge; no torch, model, or service startup."""
import asyncio
import importlib.util
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
import pytest

INTEGRATION = Path(__file__).resolve().parents[1] / 'integrations/avatar'


@pytest.fixture
def bridge():
    spec = importlib.util.spec_from_file_location('avatar_cancel_helper_test', INTEGRATION / 'avatar_cancellation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.speech_until_cancelled


class Request:
    disconnected = False

    async def is_disconnected(self):
        return self.disconnected


@pytest.mark.asyncio
async def test_success_and_failure_are_preserved(bridge, tmp_path):
    async def success():
        return {'saved': True}

    async def failure():
        raise ValueError('engine failed')

    assert await bridge(Request(), tmp_path / 'tts-request.json', success()) == {'saved': True}
    with pytest.raises(ValueError, match='engine failed'):
        await bridge(Request(), tmp_path / 'tts-request.json', failure())


@pytest.mark.asyncio
async def test_existing_flag_never_starts_operation(bridge, tmp_path):
    (tmp_path / '.cancel').touch()
    started = []

    async def operation():
        started.append(True)

    with pytest.raises(HTTPException) as exc:
        await bridge(Request(), tmp_path / 'tts-request.json', operation())
    assert exc.value.status_code == 409
    assert not started


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['flag', 'disconnect', 'outer_task'])
async def test_cancellation_awaits_operation_finalizer(bridge, tmp_path, reason):
    started, finalizing, allow_cleanup = (asyncio.Event() for _ in range(3))
    request = Request()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalizing.set()
            await allow_cleanup.wait()

    task = asyncio.create_task(bridge(request, tmp_path / 'tts-request.json', operation()))
    try:
        await asyncio.wait_for(started.wait(), 2)
        if reason == 'flag':
            (tmp_path / '.cancel').touch()
        elif reason == 'disconnect':
            request.disconnected = True
        else:
            task.cancel()
        await asyncio.wait_for(finalizing.wait(), 2)
        assert not task.done(), 'The bridge must join its operation finalizer'
        allow_cleanup.set()
        if reason == 'outer_task':
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(HTTPException) as exc:
                await task
            assert exc.value.status_code == 409
    finally:
        allow_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('cooperative', [True, False], ids=['sidecar-style', 'native-thread'])
async def test_bridge_signals_real_guard_token_without_cancelling_other_job(
    bridge, tmp_path, cooperative,
):
    from services import model_manager as mm
    from services.inference_cancellation import current_cancellation

    started, stop_seen, allow_exit, drained = (threading.Event() for _ in range(4))
    other_started, other_exit = threading.Event(), threading.Event()
    scopes = {}

    def job():
        scope = scopes['cancelled_job'] = current_cancellation()
        assert scope is not None
        started.set()
        assert scope.cancelled.wait(5), 'Cancelling await must signal the worker scope'
        stop_seen.set()
        if not cooperative:
            assert allow_exit.wait(5)
        return 'finished'

    def other_job():
        scope = scopes['other_job'] = current_cancellation()
        other_started.set()
        assert other_exit.wait(5)
        assert not scope.cancelled.is_set(), 'Cancellation must stay request scoped'
        return 'other result'

    with ThreadPoolExecutor(max_workers=2) as executor:
        other = asyncio.create_task(mm.run_on_gpu_pool_guarded(other_job, executor=executor, timeout=10))
        task = asyncio.create_task(bridge(
            Request(), tmp_path / 'tts-request.json',
            mm.run_on_gpu_pool_guarded(job, executor=executor, timeout=10, on_abandon=drained.set),
        ))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert await asyncio.to_thread(other_started.wait, 2)
            (tmp_path / '.cancel').touch()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(task, 2)
            assert exc.value.status_code == 409
            assert await asyncio.to_thread(stop_seen.wait, 2)
            assert scopes['cancelled_job'] is not scopes['other_job']
            if not cooperative:
                assert not drained.is_set(), 'HTTP 409 does not prove native inference finished'
            allow_exit.set()
            assert await asyncio.to_thread(drained.wait, 2)
            assert not other.done()
            other_exit.set()
            assert await other == 'other result'
        finally:
            allow_exit.set()
            other_exit.set()
            for pending in (task, other):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, other, return_exceptions=True)


@pytest.mark.asyncio
async def test_bridge_cancels_queued_gpu_callable_before_it_can_execute(bridge, tmp_path):
    from services import model_manager as mm

    hog_started, release_hog, ran, drained = (threading.Event() for _ in range(4))

    def hog():
        hog_started.set()
        assert release_hog.wait(5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        blocker = executor.submit(hog)
        task = asyncio.create_task(bridge(
            Request(), tmp_path / 'tts-request.json',
            mm.run_on_gpu_pool_guarded(ran.set, executor=executor, timeout=10, on_abandon=drained.set),
        ))
        try:
            assert await asyncio.to_thread(hog_started.wait, 2)
            # A submitted fence makes a post-cancel execution observable after the hog drains.
            await asyncio.sleep(0.05)
            (tmp_path / '.cancel').touch()
            with pytest.raises(HTTPException):
                await asyncio.wait_for(task, 2)
            assert drained.is_set()
            release_hog.set()
            await asyncio.wrap_future(blocker)
            await asyncio.wrap_future(executor.submit(lambda: None))
            assert not ran.is_set()
        finally:
            release_hog.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
