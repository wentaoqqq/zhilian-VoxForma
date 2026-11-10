"""Avatar queue cancel contracts. Linux process tests run with Python only.

No model download or production scheduler/service is needed. On Windows only
the physical process-group tests are skipped; queue and arbiter logic still run.
"""
import ast
import collections
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

INTEGRATION = Path(__file__).resolve().parents[1] / 'integrations/avatar'
WEB = INTEGRATION / 'vendor/tbdub_student/webui'


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(INTEGRATION))
    if sys.platform == 'win32':
        monkeypatch.setitem(sys.modules, 'fcntl', ModuleType('fcntl'))
    loaded = []
    for name, path in [('gpu_client', INTEGRATION / 'gpu_client.py'),
                       ('gpu_runtime', INTEGRATION / 'gpu_runtime.py'),
                       ('unified_jobs', WEB / 'unified_jobs.py')]:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        loaded.append(module)
    return SimpleNamespace(client=loaded[0], runtime=loaded[1], jobs=loaded[2])


@pytest.fixture
def manager(modules, tmp_path, monkeypatch):
    monkeypatch.delenv('AVATAR_GPU_COORDINATION_ENABLED', raising=False)
    root, assets = tmp_path / 'student', tmp_path / 'assets'
    root.mkdir(); assets.mkdir()
    instance = modules.jobs.JobManager(root, assets, Path(sys.executable))
    instance.started = True  # Exercise worker deterministically, never start its infinite daemon.
    return instance


def submit(manager, **fields):
    return manager.submit({'mode': 'tts', 'text': 'Cancellation fixture', 'audio_only': True, **fields})['id']


class EndWorker(BaseException):
    pass


def finite_worker(manager, monkeypatch):
    original = manager.pending.get

    def get():
        try:
            return original(block=False)
        except queue.Empty:
            raise EndWorker()

    monkeypatch.setattr(manager.pending, 'get', get)
    with pytest.raises(EndWorker):
        manager.worker()


def test_cancel_unknown_and_terminal_jobs(manager):
    with pytest.raises(KeyError):
        manager.cancel('missing')
    for state in ('succeeded', 'failed', 'cancelled'):
        jid = submit(manager)
        manager.save(jid, status=state)
        before = manager.get(jid)
        assert manager.cancel(jid) == before
        assert not (manager.out / jid / '.cancel').exists()


def test_queued_cancel_is_durable_idempotent_and_never_executed(manager, monkeypatch):
    jid = submit(manager, request_id='same-request', request_fingerprint='same-payload')
    cancelled = manager.cancel(jid)
    assert cancelled['status'] == 'cancelled'
    assert (manager.out / jid / '.cancel').is_file()
    assert json.loads((manager.out / jid / 'job.json').read_text())['status'] == 'cancelled'
    assert manager.cancel(jid) == cancelled
    assert submit(manager, request_id='same-request', request_fingerprint='same-payload') == jid
    monkeypatch.setattr(manager, 'execute', lambda _: pytest.fail('Cancelled queued job executed'))
    finite_worker(manager, monkeypatch)
    assert manager.pending.unfinished_tasks == 0


def test_active_cancel_remains_cancelling_and_rejects_late_success(manager, modules):
    jid = submit(manager)
    # Covers the dequeue/acquire window where status can still be queued.
    manager.active = jid
    result = manager.cancel(jid)
    assert result['status'] == 'cancelling'
    assert manager.cancel(jid)['cancel_requested_at'] == result['cancel_requested_at']
    with pytest.raises(modules.jobs.JobCancelled):
        manager.check_cancelled(jid)
    with pytest.raises(modules.jobs.JobCancelled):
        manager.save(jid, status='succeeded')
    assert manager.get(jid)['status'] == 'cancelling'


def test_cancelling_counts_towards_queue_capacity(manager):
    manager.capacity = 1
    jid = submit(manager)
    manager.active = jid
    manager.cancel(jid)
    with pytest.raises(Exception) as exc:
        submit(manager)
    assert type(exc.value).__name__ == 'QueueFullError'


def test_worker_finishes_cancel_only_after_lease_cleanup_and_before_next_job(
    manager, modules, monkeypatch,
):
    first, second = submit(manager), submit(manager)
    events = []
    audio = manager.out / first / 'speech.wav'
    audio.write_bytes(b'already saved audio')
    manager.save(first, audio_output=str(audio), audio_duration=1.0)

    @contextmanager
    def lease(root, jid):
        assert (root, jid) == (manager.root, first)
        try:
            yield SimpleNamespace(clear_voice=lambda: None)
        finally:
            assert manager.get(first)['status'] == 'cancelling'
            events.append('lease-cleaned')

    def command(jid, *args, **kwargs):
        manager.cancel(jid)
        manager.check_cancelled(jid)

    real_execute = manager.execute

    def execute(jid):
        if jid == first:
            return real_execute(jid)
        assert manager.get(first)['status'] == 'cancelled'
        assert events == ['lease-cleaned']
        events.append('next-started')

    monkeypatch.setenv('AVATAR_INTEGRATION_DIR', str(INTEGRATION))
    manager.save(first, tts_provider='voicestudio')
    monkeypatch.setattr(modules.jobs, 'execution_lease', lease)
    monkeypatch.setattr(manager, 'command', command)
    monkeypatch.setattr(manager, 'execute', execute)
    finite_worker(manager, monkeypatch)
    assert events == ['lease-cleaned', 'next-started']
    assert audio.read_bytes() == b'already saved audio'
    assert manager.get(first)['audio_output'] == str(audio)
    assert manager.pending.unfinished_tasks == 0


def test_finish_cancelled_waits_through_cleanup_failure_and_preserves_saved_audio(
    manager, modules, monkeypatch,
):
    jid = submit(manager)
    manager.active = jid
    manager.cancel(jid)
    audio = manager.out / jid / 'speech.wav'
    audio.write_bytes(b'finished before cancel')
    monkeypatch.setenv('AVATAR_GPU_COORDINATION_ENABLED', '1')
    polls = iter([RuntimeError('cleanup unavailable'), {'owner': {'owner': jid}, 'blocked': 'unload failed'},
                  {'owner': {}, 'blocked': None}])

    def rpc(action, **kwargs):
        assert action == 'status'
        assert manager.get(jid)['status'] == 'cancelling'
        value = next(polls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(modules.client, 'rpc', rpc)
    monkeypatch.setattr(modules.jobs.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='{"format":{"duration":"1.25"}}'))
    manager.finish_cancelled(jid)
    result = manager.get(jid)
    assert result['status'] == 'cancelled' and result['pid'] is None
    assert result['audio_output'] == str(audio) and result['audio_duration'] == 1.25
    assert audio.read_bytes() == b'finished before cancel'


def test_lease_cancel_path_is_optional_and_context_is_restored(modules, tmp_path, monkeypatch):
    client = modules.client
    calls = []

    def rpc(action, **payload):
        calls.append((action, payload))
        return {'token': 'fixture-token'}

    monkeypatch.setattr(client, 'rpc', rpc)
    outer = {'token': 'outer'}
    marker = client.current_lease.set(outer)
    try:
        for path in (None, str(tmp_path / '.cancel')):
            calls.clear()
            with client.Lease('fixture', 'avatar', cancel_path=path):
                assert client.current_lease.get()['token'] == 'fixture-token'
            acquire = calls[0][1]
            assert acquire.get('cancel_path') == path
            if path is None:
                assert 'cancel_path' not in acquire, 'Old clients must keep the old payload'
            assert calls[-1] == ('release', {'token': 'fixture-token', 'timeout': 150})
            assert client.current_lease.get() is outer
    finally:
        client.current_lease.reset(marker)


@pytest.fixture
def arbiter(tmp_path):
    # Execute the real Arbiter class without module startup's singleton watchdog,
    # socket, service HTTP calls, or /proc dependency. Physical lock is injected.
    path = INTEGRATION / 'gpu_scheduler.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Arbiter')
    locks = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *a: None)
    namespace = dict(collections=collections, threading=threading, time=time,
                     secrets=secrets, Path=Path, json=json, start_id=lambda _: 'same-process',
                     LOCK=tmp_path / 'gpu.lock', STATE=tmp_path / 'state.json', fcntl=locks)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    instance = namespace['Arbiter'].__new__(namespace['Arbiter'])
    instance.condition = threading.Condition()
    instance.pending = collections.deque()
    instance.owner = instance.fd = instance.blocked = None
    instance.recovering = False
    instance.cleanup_lock = threading.RLock()
    yield instance, locks
    if instance.fd:
        instance.fd.close()


def test_scheduler_precancel_and_grant_race_do_not_assign_owner(arbiter, tmp_path):
    instance, locks = arbiter
    flag = tmp_path / '.cancel'
    request = dict(request_id='ticket', owner='job', pid=os.getpid(), cancel_path=str(flag))
    flag.touch()
    with pytest.raises(RuntimeError, match='cancel'):
        instance.acquire(request)
    assert not instance.pending and instance.owner is None and instance.fd is None
    flag.unlink()
    locks.flock = lambda *args: flag.touch()  # Cancellation races with obtaining physical lock.
    with pytest.raises(RuntimeError, match='cancel'):
        instance.acquire(request)
    assert not instance.pending and instance.owner is None and instance.fd is None


@pytest.mark.parametrize('wait_for', ['owner', 'physical_lock'])
def test_scheduler_cancel_pending_removes_ticket_and_leaves_other_owner_alone(
    arbiter, tmp_path, wait_for,
):
    instance, locks = arbiter
    flag, waiting, done = tmp_path / '.cancel', threading.Event(), threading.Event()
    other_owner = {'owner': 'other-job', 'token': 'other-token'}
    if wait_for == 'owner':
        instance.owner = other_owner
    else:
        def busy(*args):
            raise BlockingIOError()
        locks.flock = busy
    original_wait = instance.condition.wait

    def wait(timeout=None):
        waiting.set()
        return original_wait(timeout)

    instance.condition.wait = wait
    errors = []

    def acquire():
        try:
            instance.acquire(dict(request_id='ticket', owner='cancelled-job', pid=os.getpid(), cancel_path=str(flag)))
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=acquire, daemon=True)
    thread.start()
    try:
        assert waiting.wait(2)
        flag.touch()
        assert done.wait(2.5), 'Scheduler must see cancellation without releasing the current owner'
        assert len(errors) == 1 and 'cancel' in str(errors[0]).lower()
        assert not instance.pending
        assert instance.owner is (other_owner if wait_for == 'owner' else None)
        assert instance.fd is None
    finally:
        flag.touch()
        with instance.condition:
            instance.condition.notify_all()
        thread.join(2)


@pytest.fixture
def live_scheduler(monkeypatch):
    if sys.platform != 'linux':
        pytest.skip('Real Linux scheduler process, Unix socket, peer credentials, and flock')
    # Reuse the existing independent scheduler fixture, without modifying it or
    # touching the configured production socket/state/model service.
    monkeypatch.syspath_prepend(str(INTEGRATION))
    spec = importlib.util.spec_from_file_location('avatar_coordination_cancel_test', INTEGRATION / 'test_coordination.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module.SchedulerTests('runTest')
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), 'Timed out waiting for isolated scheduler state'


@pytest.mark.parametrize('cancel_index', [0, 1], ids=['cancel-head', 'cancel-middle'])
def test_real_scheduler_cancel_preserves_remaining_fifo(live_scheduler, cancel_index):
    scheduler = live_scheduler
    incumbent = scheduler.acquire('incumbent')  # Old client: no cancel_path.
    assert incumbent['ok']
    labels = ['first', 'second', 'third']
    flags = [scheduler.root / (label + '.cancel') for label in labels]
    results, order, threads = {}, [], []

    def acquire(index):
        label = labels[index]
        try:
            result = scheduler.call('acquire', owner=label, request_id=label, cancel_path=str(flags[index]))
            results[label] = result
            if result.get('ok'):
                order.append(label)
                results[label + '-release'] = scheduler.call('release', token=result['token'])
        except Exception as exc:
            results[label] = exc

    try:
        for index in range(3):
            thread = threading.Thread(target=acquire, args=(index,), daemon=True)
            threads.append(thread); thread.start()
            eventually(lambda: scheduler.call('status')['waiting'] == index + 1)
        flags[cancel_index].touch()
        cancelled_label = labels[cancel_index]
        eventually(lambda: cancelled_label in results)
        assert results[cancelled_label]['ok'] is False
        assert 'cancel' in results[cancelled_label]['error'].lower()
        status = scheduler.call('status')
        assert status['owner']['owner'] == 'incumbent' and status['waiting'] == 2
        assert order == [], 'Cancelling a waiter must not disturb the incumbent'
        assert scheduler.proc.poll() is None
        assert scheduler.call('release', token=incumbent['token'])['ok']
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
        expected = [label for index, label in enumerate(labels) if index != cancel_index]
        assert order == expected
        assert all(results[label + '-release']['ok'] for label in expected)
        assert scheduler.call('status')['waiting'] == 0
        assert not scheduler.call('status')['owner']
    finally:
        for flag in flags:
            flag.touch()
        status = scheduler.call('status')
        if status.get('owner', {}).get('owner') == 'incumbent':
            scheduler.call('release', token=incumbent['token'])
        for thread in threads:
            thread.join(3)


def test_real_scheduler_cancel_while_physical_lock_is_held(live_scheduler):
    import fcntl

    scheduler = live_scheduler
    flag, results = scheduler.root / '.cancel', []

    def acquire():
        try:
            results.append(scheduler.call('acquire', owner='waiting', request_id='waiting', cancel_path=str(flag)))
        except Exception as exc:
            results.append(exc)

    with scheduler.lock.open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        thread = threading.Thread(target=acquire, daemon=True)
        thread.start()
        try:
            eventually(lambda: scheduler.call('status')['waiting'] == 1)
            flag.touch()
            thread.join(3)
            assert not thread.is_alive(), 'Cancel must finish while the external lock is still held'
            assert len(results) == 1 and results[0]['ok'] is False
            assert 'cancel' in results[0]['error'].lower()
            assert scheduler.call('status')['waiting'] == 0
            assert not scheduler.call('status')['owner']
        finally:
            flag.touch()
            fcntl.flock(held, fcntl.LOCK_UN)
            thread.join(3)
    # The same scheduler process must remain usable by an unchanged client.
    next_job = scheduler.acquire('next-old-client')
    assert next_job['ok']
    assert scheduler.call('release', token=next_job['token'])['ok']


@pytest.mark.skipif(sys.platform != 'linux', reason='Real Linux sessions and process groups')
@pytest.mark.parametrize('launcher_exits_on_term', [True, False])
def test_command_cancel_kills_own_group_including_term_resistant_child(
    manager, modules, tmp_path, launcher_exits_on_term,
):
    jid = submit(manager)
    manager.save(jid, status='running')
    manager.active = jid
    marker = tmp_path / 'pids.json'
    child_code = 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready",flush=True); time.sleep(60)'
    script = tmp_path / 'launcher.py'
    script.write_text(
        'import subprocess,sys,os,json,signal,time\n'
        + ('' if launcher_exits_on_term else 'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n')
        + f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}],stdout=subprocess.PIPE,text=True)\n'
        + 'assert p.stdout.readline().strip()=="ready"\n'
        + f'open({str(marker)!r},"w").write(json.dumps([os.getpid(),p.pid]))\n'
        + 'time.sleep(60)\n', encoding='utf-8',
    )
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    done, errors, pids = threading.Event(), [], []

    def run():
        try:
            with (tmp_path / 'command.log').open('w') as log:
                manager.command(jid, [sys.executable, script], tmp_path, log, timeout=30)
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def live(pid):
        try:
            return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
        except FileNotFoundError:
            return False

    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), 'Fixture launcher did not become ready'
        pids = json.loads(marker.read_text())
        manager.cancel(jid)
        assert done.wait(8), 'Command must TERM/KILL and join, rather than abandon its subprocess'
        assert len(errors) == 1 and isinstance(errors[0], modules.jobs.JobCancelled)
        assert not any(live(pid) for pid in pids), 'Command returned before its process group exited'
        assert manager.get(jid)['pid'] is None
        assert unrelated.poll() is None, 'Another task must survive cancellation'
    finally:
        if pids:
            try:
                os.killpg(pids[0], signal.SIGKILL)
            except ProcessLookupError:
                pass
        unrelated.kill(); unrelated.wait()
        thread.join(5)
