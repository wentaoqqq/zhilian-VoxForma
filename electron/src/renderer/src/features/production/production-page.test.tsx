import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { ProductionPage } from './production-page';

const { request, fetchFile } = vi.hoisted(() => ({ request: vi.fn(), fetchFile: vi.fn() }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', () => ({
  apiJson: request,
  apiFetch: fetchFile,
  apiPath: (path: string) => path,
  describeError: (e: unknown) => String(e),
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
}));

const source = {
  id: 'source',
  filename: 'Product.mp4',
  duration: 12,
  width: 1920,
  height: 1080,
  bytes: 1200,
};
const outro = { ...source, id: 'outro', filename: 'Outro.mp4', duration: 2 };
const savedPlan = {
  schema_version: 1,
  plugin_id: 'general',
  source_id: source.id,
  outro_id: outro.id,
  title: 'Opening',
  cover_title: 'Cover',
  publication_title: 'Publication',
  brand_text: '',
  subtitle_mode: 'keep',
  captions: [],
  cover_time: 0,
};
const configResponses: Record<string, unknown> = {
  '/production/plugins': [{ id: 'general', name_key: 'production.generalPlugin' }],
  '/production/ai/status': { ready: true, provider_display_name: 'Local test model' },
};

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
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (configResponses[path]) return configResponses[path];
    if (path === '/production/assets') return [source, outro];
    if (path === '/production/plugins')
      return [{ id: 'general', name_key: 'production.generalPlugin' }];
    if (path === '/production/ai/status')
      return { ready: true, provider_display_name: 'Local test model' };
    if (path === '/production/runs' && !init) return [];
    if (path.startsWith('/production/assets?')) return source;
    if (path === '/production/runs' && init?.method === 'POST') return { id: 'new-run' };
    return [];
  });
});

it('requires a source, outro and titles before submitting', async () => {
  mount();
  expect(screen.getByRole('button', { name: 'production.generate' })).toBeDisabled();
  await screen.findByTestId('production-page');
  expect(request).not.toHaveBeenCalledWith(
    '/production/runs',
    expect.objectContaining({ method: 'POST' }),
  );
});

it('shows previews and lets library videos fill either role', async () => {
  const { container } = mount();
  fireEvent.click(screen.getByRole('button', { name: 'production.library' }));
  const card = (await screen.findByText('Product.mp4')).closest('article')!;
  expect(within(card).getByText(/1920 × 1080/)).toBeInTheDocument();
  expect(card.querySelector('video')?.getAttribute('src')).toBe(
    '/production/assets/source/preview',
  );
  fireEvent.click(within(card).getByRole('button', { name: 'production.source' }));
  expect(container.querySelector('video')?.getAttribute('src')).toBe(
    '/production/assets/source/preview',
  );
  expect(screen.getByRole('button', { name: 'production.generate' })).toBeDisabled();
});

it('uploads a real file body and updates the current source', async () => {
  const { container } = mount();
  const file = new File(['test file bytes'], 'New product.mp4', { type: 'video/mp4' });
  fireEvent.change(screen.getByLabelText('gallery.upload', { selector: 'input' }), {
    target: { files: [file] },
  });
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/production/assets?filename=New+product.mp4',
      expect.objectContaining({ method: 'POST', body: file }),
    ),
  );
  await waitFor(() =>
    expect(container.querySelector('video')).toHaveAttribute(
      'src',
      '/production/assets/source/preview',
    ),
  );
  expect(screen.getByLabelText('production.opening')).toHaveValue('New product');
});

it('reopens an immutable plan for editing and submits through the production queue', async () => {
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (configResponses[path]) return configResponses[path];
    if (path === '/production/assets') return [source, outro];
    if (path === '/production/runs' && !init)
      return [
        {
          id: 'failed',
          status: 'failed',
          error: 'Stopped',
          steps: {},
          artifacts: {},
          plan: savedPlan,
        },
      ];
    return { id: 'new' };
  });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'production.edit' }));
  expect(screen.getByLabelText('production.opening')).toHaveValue('Opening');
  expect(screen.getByRole('button', { name: 'production.generate' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'production.generate' }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith('/production/runs', {
      method: 'POST',
      body: JSON.stringify(savedPlan),
    }),
  );
});

it('exposes cancellation for active work and a download for completed deliveries', async () => {
  request.mockImplementation(
    async (path: string) =>
      configResponses[path] ||
      (path === '/production/assets'
        ? [source, outro]
        : [
            {
              id: 'active',
              status: 'running',
              steps: { render: 'running' },
              plan: savedPlan,
              artifacts: {},
            },
            {
              id: 'done',
              status: 'done',
              steps: { render: 'done' },
              plan: savedPlan,
              artifacts: { video: 'video.mp4' },
            },
          ]),
  );
  const { container } = mount();
  fireEvent.click(await screen.findByRole('button', { name: 'common.cancel' }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith('/production/runs/active/cancel', { method: 'POST' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'production.deliveries' }));
  expect(screen.getByRole('button', { name: 'production.download' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'common.cancel' })).not.toBeInTheDocument();
  expect(container.querySelector('video')?.getAttribute('src')).toBe(
    '/production/runs/done/files/video',
  );
});

it('AI edits the current draft and only auto-submits when explicitly requested', async () => {
  localStorage.setItem('voxforma.production.draft.v1', JSON.stringify(savedPlan));
  const next = { ...savedPlan, title: 'AI opening' };
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (configResponses[path]) return configResponses[path];
    if (path === '/production/assets') return [source, outro];
    if (path === '/production/runs') return [];
    if (path === '/production/ai/plan')
      return {
        plan: next,
        explanation: 'Revised opening',
        issues: [],
        ready: true,
        run: JSON.parse(String(init?.body)).submit ? { id: 'new' } : null,
      };
    return [];
  });
  mount();
  fireEvent.change(screen.getByLabelText('production.ai.instruction'), {
    target: { value: 'Use a concise opening' },
  });
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'production.ai.plan' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'production.ai.plan' }));
  await waitFor(() =>
    expect(screen.getByLabelText('production.opening')).toHaveValue('AI opening'),
  );
  expect(request).toHaveBeenCalledWith('/production/ai/plan', {
    method: 'POST',
    body: JSON.stringify({ draft: savedPlan, instruction: 'Use a concise opening', submit: false }),
  });
  expect(request).not.toHaveBeenCalledWith(
    '/production/runs',
    expect.objectContaining({ method: 'POST' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'production.ai.generate' }));
  await screen.findByText('production.ai.queued');
  expect(request).toHaveBeenCalledWith('/production/ai/plan', {
    method: 'POST',
    body: JSON.stringify({ draft: next, instruction: 'Use a concise opening', submit: true }),
  });
});

it('changing the product resets the previous product subtitles and titles', async () => {
  localStorage.setItem(
    'voxforma.production.draft.v1',
    JSON.stringify({
      ...savedPlan,
      subtitle_mode: 'provided',
      captions: [{ start: 0, end: 10, text: 'Old product claim' }],
    }),
  );
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'production.library' }));
  const card = (await screen.findByText('Outro.mp4')).closest('article')!;
  fireEvent.click(within(card).getByRole('button', { name: 'production.source' }));
  expect(screen.getByLabelText('production.opening')).toHaveValue('Outro');
  fireEvent.click(screen.getByRole('button', { name: 'production.generate' }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith('/production/runs', {
      method: 'POST',
      body: JSON.stringify({
        ...savedPlan,
        source_id: 'outro',
        title: 'Outro',
        cover_title: 'Outro',
        publication_title: 'Outro',
      }),
    }),
  );
});

it('keeps manual production available when AI has no provider', async () => {
  localStorage.setItem('voxforma.production.draft.v1', JSON.stringify(savedPlan));
  request.mockImplementation(async (path: string) => {
    if (path === '/production/ai/status') return { ready: false };
    if (configResponses[path]) return configResponses[path];
    if (path === '/production/assets') return [source, outro];
    return [];
  });
  mount();
  await screen.findByText('production.ai.configure', { exact: false });
  expect(screen.getByRole('button', { name: 'production.ai.plan' })).toBeDisabled();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'production.generate' })).toBeEnabled(),
  );
});
