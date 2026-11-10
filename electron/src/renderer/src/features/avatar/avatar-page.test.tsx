import type { ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AvatarPage } from './avatar-page';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.api,
  apiPath: (path: string) => '/proxy' + path,
  describeError: (error: Error) => error.message,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('./character-picker', () => ({ CharacterPicker: () => null }));
vi.mock('./characters-api', () => ({
  useCharacters: () => ({ data: [{ id: 'character-1', available: true }] }),
  useCharacterSelection: () => ['character-1', vi.fn()],
}));
vi.mock('@shared/components/SearchableSelect', () => ({ default: () => null }));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
}));
vi.mock('@/components/engine-notice', () => ({ EngineNotice: () => null }));
vi.mock('@/hooks/use-engines', () => ({ useEngines: () => ({ activeTts: null }) }));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => ({ data: [] }) }));
vi.mock('@/hooks/use-clone-readiness', () => ({ useCloneReadiness: () => 'engine' }));
vi.mock('@/hooks/use-reference-transcript', () => ({ useReferenceTranscript: () => ({}) }));
vi.mock('@/hooks/use-tts-readiness', () => ({ isPresetOnly: () => true }));
vi.mock('@/hooks/use-generate', () => ({ manualProfileTranscript: vi.fn() }));
vi.mock('@/lib/api/generate', () => ({ toGenerateForm: vi.fn() }));
vi.mock('@/lib/store/clone-settings', () => ({
  useCloneSettings: () => ({ selectedProfileId: null }),
  setCloneSetting: vi.fn(),
}));
vi.mock('@/lib/store/reference', () => ({ useReference: () => ({ file: null }) }));
vi.mock('@/lib/store/workspace', () => ({
  useWorkspace: () => ({ editingProfileId: null }),
  setWorkspace: vi.fn(),
}));
vi.mock('@/features/clone/voice-setup', () => ({ VoiceSetup: () => null }));
vi.mock('@/features/clone/reference-panel', () => ({ ReferencePanel: () => null }));
vi.mock('@/features/clone/edit-profile', () => ({ EditProfile: () => null }));
vi.mock('@/features/clone/script-panel', () => ({ ScriptPanel: () => null }));
vi.mock('@/features/clone/action-bar', () => ({ VoiceControls: () => null }));
vi.mock('@/features/clone/quality-controls', () => ({ QualityControls: () => null }));
vi.mock('@/features/clone/emotion-controls', () => ({ EmotionControls: () => null }));
vi.mock('@/features/clone/engine-language-picker', () => ({ EngineLanguagePicker: () => null }));
vi.mock('@/features/clone/preset-voice-picker', () => ({ PresetVoicePicker: () => null }));

type Job = { id: string; status: string; audio_output?: string; output?: string };
let jobs: Job[];
let pollError: Error | null;
const snapshot = () => ({
  jobs,
  queue: { running: 1, waiting: 1, capacity: 16, full: false },
});
const row = (id: string) => {
  const article = screen.getByText(id).closest('article');
  if (!article) throw new Error('Missing job row: ' + id);
  return article;
};
const pending = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
};
const mount = () =>
  act(async () => {
    render(<AvatarPage />);
  });
const poll = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(2500);
  });
const clickCancel = (id: string) =>
  fireEvent.click(within(row(id)).getByRole('button', { name: 'common.cancel' }));

beforeEach(() => {
  vi.useFakeTimers();
  jobs = [
    { id: 'queued-1', status: 'queued' },
    { id: 'running-2', status: 'running', audio_output: 'audio.wav' },
  ];
  pollError = null;
  mocks.api.mockReset();
  mocks.api.mockImplementation((path: string) => {
    if (path === '/api/avatar/files') return Promise.resolve({ videos: [], audios: [] });
    if (path === '/api/avatar/jobs')
      return pollError ? Promise.reject(pollError) : Promise.resolve(snapshot());
    throw new Error('Unexpected request: ' + path);
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('posts the selected ID once and keeps each cancellation independent until the server settles', async () => {
  const first = pending<Job>();
  const second = pending<Job>();
  await mount();
  mocks.api
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);
  fireEvent.click(within(row('running-2')).getByRole('button', { name: 'common.cancel' }));
  const cancelling = within(row('running-2')).getByRole('button', { name: 'avatar.cancelling' });
  expect(cancelling).toBeDisabled();
  fireEvent.click(cancelling);
  expect(within(row('queued-1')).getByRole('button', { name: 'common.cancel' })).toBeEnabled();
  clickCancel('queued-1');
  const posts = mocks.api.mock.calls.filter(([, init]) => init?.method === 'POST');
  expect(posts).toEqual([
    ['/api/avatar/jobs/running-2/cancel', { method: 'POST' }],
    ['/api/avatar/jobs/queued-1/cancel', { method: 'POST' }],
  ]);
  await act(async () => {
    first.resolve({ id: 'running-2', status: 'cancelling' });
    second.resolve({ id: 'queued-1', status: 'cancelled' });
  });
  expect(
    within(row('running-2')).getByRole('button', { name: 'avatar.cancelling' }),
  ).toBeDisabled();
  expect(within(row('queued-1')).queryByRole('button', { name: 'common.cancel' })).toBeNull();
  expect(row('running-2').querySelector('audio')).toHaveAttribute(
    'src',
    '/proxy/api/avatar/jobs/running-2/audio',
  );
  jobs = jobs.map((job) => ({ ...job, status: 'cancelled' }));
  await poll();
  expect(within(row('running-2')).getByText('batch.status_cancelled')).toBeInTheDocument();
  expect(within(row('running-2')).queryByText('batch.status_failed')).toBeNull();
  expect(
    within(row('running-2')).getByRole('link', { name: 'avatar.download · avatar.audio' }),
  ).toHaveAttribute('href', '/proxy/api/avatar/jobs/running-2/audio-download');
  expect(within(row('running-2')).getByRole('button', { name: 'avatar.retryVideo' })).toBeEnabled();
});

it.each(['succeeded', 'failed', 'cancelled'])(
  'never offers cancellation for %s and can reuse its audio',
  async (status) => {
    jobs = [{ id: 'terminal', status, audio_output: 'voice.wav', output: 'video.mp4' }];
    await mount();
    expect(within(row('terminal')).queryByRole('button', { name: 'common.cancel' })).toBeNull();
    expect(within(row('terminal')).queryByRole('button', { name: 'avatar.cancelling' })).toBeNull();
    const send = pending<{ id: string }>();
    mocks.api.mockImplementationOnce(() => send.promise);
    fireEvent.click(within(row('terminal')).getByRole('button', { name: 'avatar.retryVideo' }));
    const [path, init] = mocks.api.mock.calls.at(-1)!;
    expect(path).toBe('/api/avatar/jobs');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toMatchObject({
      mode: 'audio',
      reuse_audio_job: 'terminal',
      character_id: 'character-1',
      fps: 25,
    });
    if (status === 'succeeded') expect(row('terminal').querySelector('video')).toBeInTheDocument();
  },
);

it.each(['queued', 'running', 'cancelling'])(
  'does not offer video retry during %s even with generated audio',
  async (status) => {
    jobs = [{ id: 'active', status, audio_output: 'voice.wav' }];
    await mount();
    expect(within(row('active')).queryByRole('button', { name: 'avatar.retryVideo' })).toBeNull();
    if (status === 'cancelling') {
      expect(
        within(row('active')).getByRole('button', { name: 'avatar.cancelling' }),
      ).toBeDisabled();
    } else {
      expect(within(row('active')).getByRole('button', { name: 'common.cancel' })).toBeEnabled();
    }
  },
);

it('can cancel while a new submission is busy', async () => {
  jobs.push({ id: 'old', status: 'cancelled', audio_output: 'voice.wav' });
  await mount();
  const submission = pending<{ id: string }>();
  mocks.api.mockImplementationOnce(() => submission.promise);
  fireEvent.click(within(row('old')).getByRole('button', { name: 'avatar.retryVideo' }));
  expect(within(row('old')).getByRole('button', { name: 'avatar.retryVideo' })).toBeDisabled();
  expect(within(row('running-2')).getByRole('button', { name: 'common.cancel' })).toBeEnabled();
  mocks.api.mockResolvedValueOnce({ id: 'running-2', status: 'cancelled' });
  await act(async () => {
    clickCancel('running-2');
  });
  expect(mocks.api).toHaveBeenLastCalledWith('/api/avatar/jobs/running-2/cancel', {
    method: 'POST',
  });
  expect(within(row('running-2')).getByText('batch.status_cancelled')).toBeInTheDocument();
});

it('retains jobs and their outputs across a failed poll and resumes polling', async () => {
  await mount();
  pollError = new Error('poll unavailable');
  await poll();
  expect(screen.getByRole('alert')).toHaveTextContent('poll unavailable');
  expect(row('running-2').querySelector('audio')).toBeInTheDocument();
  expect(within(row('queued-1')).getByRole('button', { name: 'common.cancel' })).toBeEnabled();
  pollError = null;
  jobs = jobs.map((job) => ({ ...job, status: 'cancelled' }));
  await poll();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(within(row('running-2')).getByText('batch.status_cancelled')).toBeInTheDocument();
});

it('allows cancellation to be retried after a request failure without losing outputs', async () => {
  await mount();
  mocks.api.mockRejectedValueOnce(new Error('cancel unavailable'));
  await act(async () => {
    clickCancel('running-2');
  });
  expect(screen.getByRole('alert')).toHaveTextContent('cancel unavailable');
  expect(row('running-2').querySelector('audio')).toBeInTheDocument();
  expect(within(row('running-2')).getByRole('button', { name: 'common.cancel' })).toBeEnabled();
  mocks.api.mockResolvedValueOnce({ id: 'running-2', status: 'cancelled' });
  await act(async () => {
    clickCancel('running-2');
  });
  expect(within(row('running-2')).getByText('batch.status_cancelled')).toBeInTheDocument();
});

it('does not let a poll started before cancellation restore an obsolete running state', async () => {
  await mount();
  const stale = pending<ReturnType<typeof snapshot>>();
  mocks.api.mockImplementationOnce(() => stale.promise);
  await poll();
  mocks.api.mockResolvedValueOnce({
    id: 'running-2',
    status: 'cancelled',
    audio_output: '',
    output: '',
  });
  await act(async () => {
    clickCancel('running-2');
  });
  await act(async () => {
    stale.resolve(snapshot());
  });
  expect(within(row('running-2')).getByText('batch.status_cancelled')).toBeInTheDocument();
  expect(within(row('running-2')).queryByRole('button', { name: 'common.cancel' })).toBeNull();
  expect(row('running-2').querySelector('audio')).toBeInTheDocument();
});
