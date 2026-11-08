import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { ProductionPage } from './production-page';
const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', () => ({
  apiJson: request,
  apiPath: (p: string) => p,
  describeError: String,
}));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => ({ data: [] }) }));
vi.mock('@shared/components/VoiceSelector', () => ({
  default: () => <div>Native voice selector</div>,
}));
vi.mock('@shared/components/SearchableSelect', () => ({
  default: ({ value, onChange, options, ariaLabel }: any) => (
    <div aria-label={ariaLabel}>
      {options.map((o: any) => (
        <button key={o.value} aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  ),
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
}));
const one = {
  id: 'one',
  filename: 'First.mp4',
  kind: 'video',
  duration: 4,
  width: 1080,
  height: 1920,
  bytes: 1000,
};
const two = { ...one, id: 'two', filename: 'Second.mp4' };
const outro = { ...one, id: 'outro', filename: 'Outro.mp4' };
const brand = {
  id: 'brand',
  name: 'My brand',
  pack_id: 'roundtable',
  notes: 'Confirmed facts',
  outro_ids: ['outro'],
  material_ids: [],
  sticker_ids: [],
  voice_profile: null,
  tts_engine: null,
};
let assets: any[], batches: any[], ready: boolean;
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProductionPage />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  assets = [one, two, outro];
  batches = [];
  ready = true;
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/production/assets') return assets;
    if (path === '/production/brands') return [brand];
    if (path === '/production/agent/status')
      return {
        planner: { ready },
        vision: { ready },
        runtime: { installed: true, packs: ['roundtable'] },
      };
    if (path === '/production/batches' && !init) return batches;
    if (path === '/production/batches' && init?.method === 'POST') return { id: 'new-batch' };
    if (path.startsWith('/production/assets?')) {
      const id = 'new' + assets.length;
      const asset = {
        ...one,
        id,
        filename: new URLSearchParams(path.split('?')[1]).get('filename'),
      };
      assets.push(asset);
      return asset;
    }
    return {};
  });
});
it('starts all pending inputs with no per-video title or outro form', async () => {
  mount();
  const start = await screen.findByRole('button', { name: 'production.batch.start · 2' });
  await waitFor(() => expect(start).toBeEnabled());
  expect(screen.queryByLabelText('production.opening')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('production.coverTime')).not.toBeInTheDocument();
  fireEvent.click(start);
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/production/batches',
      expect.objectContaining({ method: 'POST' }),
    ),
  );
  const body = JSON.parse(
    request.mock.calls.find(([p, i]) => p === '/production/batches' && i?.method === 'POST')![1]
      .body,
  );
  expect(body.source_ids).toEqual(['one', 'two']);
  expect(body.brand_id).toBe('brand');
  expect(body.request_id).toBeTruthy();
});
it('uploads multiple real files and selects the new batch', async () => {
  mount();
  const a = new File(['a'], 'A.mp4'),
    b = new File(['b'], 'B.mp4');
  fireEvent.change(screen.getByTestId('batch-upload'), { target: { files: [a, b] } });
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/production/assets?filename=B.mp4',
      expect.objectContaining({ body: b }),
    ),
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'production.batch.start · 2' })).toBeEnabled(),
  );
  expect(screen.getByRole('checkbox', { name: 'A.mp4' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'First.mp4' })).not.toBeChecked();
});
it('clear stays empty rather than silently selecting everything', async () => {
  mount();
  await screen.findByRole('button', { name: 'production.batch.start · 2' });
  fireEvent.click(screen.getByRole('button', { name: 'common.clear' }));
  expect(screen.getByRole('button', { name: 'production.batch.start · 0' })).toBeDisabled();
});
it('does not regenerate completed or active inputs', async () => {
  batches = [
    {
      id: 'old',
      status: 'done',
      items: [{ source_id: 'one', filename: one.filename, status: 'done', artifacts: {} }],
      log: [],
      request: { options: { workflow: 'product' }, brand },
    },
  ];
  mount();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'production.batch.start · 1' })).toBeEnabled(),
  );
  expect(screen.queryByRole('checkbox', { name: 'First.mp4' })).not.toBeInTheDocument();
});
it('shows missing setup without calling AI on page open', async () => {
  ready = false;
  mount();
  await screen.findByText('production.batch.setupHint');
  expect(await screen.findByRole('button', { name: 'production.batch.start · 2' })).toBeDisabled();
  expect(request.mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(0);
});
it('previews real media', async () => {
  const { baseElement } = mount();
  await screen.findByRole('button', { name: 'common.play First.mp4' });
  fireEvent.click(screen.getByRole('button', { name: 'common.play First.mp4' }));
  await waitFor(() =>
    expect(baseElement.querySelector('video')).toHaveAttribute(
      'src',
      '/production/assets/one/preview',
    ),
  );
});
it('offers failed-only retry and completed downloads together', async () => {
  batches = [
    {
      id: 'mixed',
      status: 'done',
      items: [
        {
          source_id: 'one',
          filename: one.filename,
          status: 'done',
          artifacts: { video: 'video.mp4' },
        },
        {
          source_id: 'two',
          filename: two.filename,
          status: 'failed',
          error: 'Missing useful footage',
          artifacts: {},
        },
      ],
      log: [],
      request: { options: { workflow: 'product' }, brand },
    },
  ];
  mount();
  expect(await screen.findByText('Missing useful footage')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'production.download' })).toHaveAttribute(
    'href',
    '/production/batches/mixed/download',
  );
  fireEvent.click(screen.getByRole('button', { name: 'production.batch.retryFailed' }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith('/production/batches/mixed/retry', { method: 'POST' }),
  );
});
