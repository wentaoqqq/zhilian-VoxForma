import type { ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AvatarPage } from './avatar-page';
import type { EngineBackend } from '@/lib/api/types';
import type { CloneSettings } from '@/lib/store/clone-settings';

const mocks = vi.hoisted(() => ({ api: vi.fn(), form: vi.fn(), engine: null as EngineBackend | null, settings: {} as CloneSettings, presetOnly: true }));
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
vi.mock('@/hooks/use-engines', () => ({ useEngines: () => ({ activeTts: mocks.engine }) }));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => ({ data: [] }) }));
vi.mock('@/hooks/use-clone-readiness', () => ({ useCloneReadiness: () => mocks.engine ? null : 'engine' }));
vi.mock('@/hooks/use-reference-transcript', () => ({ useReferenceTranscript: () => ({}) }));
vi.mock('@/hooks/use-tts-readiness', () => ({ isPresetOnly: () => mocks.presetOnly }));
vi.mock('@/hooks/use-generate', () => ({ manualProfileTranscript: vi.fn() }));
vi.mock('@/lib/api/generate', () => ({ toGenerateForm: mocks.form }));
vi.mock('@/lib/store/clone-settings', () => ({
  useCloneSettings: () => mocks.settings,
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
  mocks.engine = null;
  mocks.presetOnly = true;
  mocks.settings = {
    selectedProfileId: null, text: 'Emotion check.', instruct: '', direction: '',
    emotionMode: 'none', emotionProfileId: null, emotionText: '', emotionAlpha: 0.6,
    emotionVector: [0, 0, 0, 0, 0, 0, 0, 0],
    steps: 16, cfg: 2, speed: 1, denoise: true, tShift: 0.1, posTemp: 5,
    classTemp: 0, layerPenalty: 5, postprocess: true, duration: '', wavBits: 16,
    effectPreset: 'broadcast',
  } as CloneSettings;
  mocks.form.mockReset().mockReturnValue(new FormData());
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

it.each([
  ['indextts20', 0, 'avatar.generateVideo', false],
  ['indextts20', 1, 'avatar.audioOnly', true],
  ['indextts2', 1, 'avatar.generateVideo', false],
  ['indextts2', 0, 'avatar.audioOnly', true],
] as const)('serializes shared emotion audio for %s at strength %s (%s)', async (id, emotionAlpha, action, audioOnly) => {
  const { toGenerateForm } = await vi.importActual<typeof import('@/lib/api/generate')>('@/lib/api/generate');
  mocks.form.mockImplementation(toGenerateForm);
  mocks.engine = { id, supports_emotion: true } as EngineBackend;
  mocks.presetOnly = false;
  Object.assign(mocks.settings, { selectedProfileId: 'speaker', emotionMode: 'audio', emotionProfileId: 'emotion-profile', emotionAlpha, emotionText: 'stale text' });
  await mount();
  mocks.api.mockResolvedValueOnce({ id: 'shared-job' });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: action })));
  const post = mocks.api.mock.calls.find(([path, init]) => path === '/api/avatar/jobs' && init?.method === 'POST');
  const snapshot = JSON.parse(post![1].body);
  expect(snapshot).toMatchObject({
    voice_profile: 'speaker', tts_engine: id, audio_only: audioOnly, emotion_enabled: false,
    emotion: '', native_form: { emo_profile_id: 'emotion-profile', emo_alpha: String(emotionAlpha) },
  });
  expect(snapshot.native_form).not.toHaveProperty('emo_text');
  expect(snapshot.native_form).not.toHaveProperty('emo_vector');
  expect(mocks.settings.selectedProfileId).toBe('speaker');
});

it('gives explicit avatar audio priority over shared audio and its strength', async () => {
  const { toGenerateForm } = await vi.importActual<typeof import('@/lib/api/generate')>('@/lib/api/generate');
  mocks.form.mockImplementation(toGenerateForm);
  mocks.engine = { id: 'indextts20', supports_emotion: true } as EngineBackend;
  Object.assign(mocks.settings, { emotionMode: 'audio', emotionProfileId: 'shared-emotion', emotionAlpha: 0.2 });
  await mount();
  fireEvent.click(screen.getByLabelText('avatar.emotionEnabled'));
  expect(screen.getByRole('button', { name: 'avatar.audioOnly' })).toBeDisabled();
  mocks.api.mockResolvedValueOnce({ id: 'explicit.wav' });
  await act(async () => fireEvent.change(document.getElementById('avatar-emotion-upload')!, { target: { files: [new File(['audio'], 'explicit.wav')] } }));
  mocks.api.mockResolvedValueOnce({ id: 'override-job' });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'avatar.audioOnly' })));
  const post = mocks.api.mock.calls.find(([path, init]) => path === '/api/avatar/jobs' && init?.method === 'POST');
  const snapshot = JSON.parse(post![1].body);
  expect(snapshot).toMatchObject({ emotion_enabled: true, emotion: 'explicit.wav', emotion_weight: 1 });
  for (const key of ['emo_profile_id', 'emo_alpha', 'emo_text', 'emo_vector']) expect(snapshot.native_form).not.toHaveProperty(key);
});

it.each([
  ['indextts20', 'audio', null, true],
  ['indextts2', 'audio', '', true],
  ['indextts20', 'none', null, false],
  ['omnivoice', 'audio', null, false],
  ['other-emotion-engine', 'audio', 'stale-emotion', false],
] as const)('handles empty or stale shared reference for %s in %s mode', async (id, emotionMode, emotionProfileId, disabled) => {
  const { toGenerateForm } = await vi.importActual<typeof import('@/lib/api/generate')>('@/lib/api/generate');
  mocks.form.mockImplementation(toGenerateForm);
  mocks.engine = { id, supports_emotion: true } as EngineBackend;
  Object.assign(mocks.settings, { emotionMode, emotionProfileId });
  await mount();
  for (const action of ['avatar.audioOnly', 'avatar.generateVideo']) {
    const button = screen.getByRole('button', { name: action });
    expect(button).toHaveProperty('disabled', disabled);
  }
  mocks.api.mockResolvedValueOnce({ id: 'neutral-job' });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'avatar.audioOnly' })));
  if (disabled) expect(mocks.form).not.toHaveBeenCalled();
  else {
    const post = mocks.api.mock.calls.find(([path, init]) => path === '/api/avatar/jobs' && init?.method === 'POST');
    const snapshot = JSON.parse(post![1].body);
    expect(snapshot.emotion_enabled).toBe(false);
    expect(snapshot.native_form).not.toHaveProperty('emo_profile_id');
    expect(snapshot.native_form).not.toHaveProperty('emo_alpha');
  }
});

it.each(['indextts2', 'indextts20'])(
  'offers emotion audio for %s and preserves it when switching between IndexTTS versions',
  async (id) => {
    mocks.engine = { id } as EngineBackend;
    let view!: ReturnType<typeof render>;
    await act(async () => { view = render(<AvatarPage />); });
    fireEvent.click(screen.getByLabelText('avatar.emotionEnabled'));
    expect(screen.getByLabelText('avatar.emotionEnabled')).toBeChecked();
    mocks.engine = { id: id === 'indextts2' ? 'indextts20' : 'indextts2' } as EngineBackend;
    await act(async () => { view.rerender(<AvatarPage />); });
    expect(screen.getByLabelText('avatar.emotionEnabled')).toBeChecked();
    mocks.engine = { id: 'omnivoice' } as EngineBackend;
    await act(async () => { view.rerender(<AvatarPage />); });
    expect(screen.queryByLabelText('avatar.emotionEnabled')).toBeNull();
    mocks.engine = { id } as EngineBackend;
    await act(async () => { view.rerender(<AvatarPage />); });
    expect(screen.getByLabelText('avatar.emotionEnabled')).not.toBeChecked();
  },
);

it.each(['indextts2', 'indextts20', 'omnivoice'])(
  'submits native generation with the dynamically selected %s engine',
  async (id) => {
    mocks.engine = { id, supports_emotion: true } as EngineBackend;
    await mount();
    const emotionToggle = screen.queryByLabelText('avatar.emotionEnabled');
    if (emotionToggle) {
      fireEvent.click(emotionToggle);
      mocks.api.mockResolvedValueOnce({ id: 'emotion.wav' });
      await act(async () => {
        fireEvent.change(document.getElementById('avatar-emotion-upload')!, {
          target: { files: [new File(['audio'], 'emotion.wav')] },
        });
      });
    }
    mocks.api.mockResolvedValueOnce({ id: 'new-job' });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'avatar.audioOnly' }));
    });
    const post = mocks.api.mock.calls.find(([path, init]) => path === '/api/avatar/jobs' && init?.method === 'POST');
    expect(post).toBeDefined();
    expect(JSON.parse(post![1].body)).toMatchObject({
      tts_engine: id, native_form: {}, audio_only: true,
      emotion_enabled: Boolean(emotionToggle), emotion: emotionToggle ? 'emotion.wav' : '',
    });
    expect(mocks.form).toHaveBeenCalledOnce();
  },
);

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
