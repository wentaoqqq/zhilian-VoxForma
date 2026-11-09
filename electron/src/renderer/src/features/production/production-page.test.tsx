import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  default: ({ value, onChange, options, ariaLabel, disabled, menuPortal }: any) => (
    <div role="group" aria-label={ariaLabel} data-portal={menuPortal}>
      {options.map((o: any) => (
        <button
          type="button"
          key={o.value}
          disabled={disabled}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  ),
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
}));
const key = (value: string) => 'production.workspace.' + value;
const product = {
  id: 'product-a',
  name: 'Steel houses',
  pack_id: 'roundtable',
  notes: 'Confirmed facts',
  outro_ids: ['outro'],
  material_ids: [],
  sticker_ids: [],
  voice_profile: null,
  tts_engine: null,
  categories: [{ id: 'construction', name: 'Construction', description: 'Building footage' }],
  video_types: [
    { id: 'showcase', name: 'Showcase', workflow: 'product', instruction: '' },
    {
      id: 'voiceover',
      name: 'Voiceover',
      workflow: 'narration',
      instruction: 'Use relevant footage',
    },
  ],
};
const one = {
  id: 'one',
  filename: 'First.mp4',
  kind: 'video',
  product_id: product.id,
  category_id: 'construction',
  video_type_id: 'showcase',
  classification_status: 'classified',
  role: 'input',
};
const two = {
  ...one,
  id: 'two',
  filename: 'Second.mp4',
  category_id: null,
  classification_status: 'pending',
};
const outro = { ...one, id: 'outro', filename: 'Outro.mp4', role: 'outro' };
const session = {
  id: 'chat-a',
  title: 'Existing discussion',
  product_id: product.id,
  video_type_id: 'showcase',
  messages: [
    { id: 'm1', role: 'user', text: 'Previous idea' },
    { id: 'm2', role: 'assistant', text: 'Previous answer' },
  ],
  batches: [],
};
let assets: any[], products: any[], sessions: any[], details: Record<string, any>;
let ready: boolean, visionReady: boolean, installed: boolean, packs: string[];
let client: QueryClient;
function mount() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProductionPage />
    </QueryClientProvider>,
  );
}
function pick(label: string, option: string) {
  fireEvent.click(
    within(screen.getByRole('group', { name: label })).getByRole('button', { name: option }),
  );
}
function posts(path: string) {
  return request.mock.calls.filter(([p, i]) => p === path && i?.method === 'POST');
}
function lastBody(path: string) {
  return JSON.parse(posts(path).at(-1)![1].body);
}
async function readyToSend() {
  const start = await screen.findByRole('button', { name: 'production.batch.start' });
  await waitFor(() => expect(start).toBeEnabled());
  await waitFor(() => expect(client.isFetching({ queryKey: ['production-assets'] })).toBe(0));
  return start;
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  assets = [one, two, outro];
  products = [structuredClone(product)];
  sessions = [];
  details = {};
  ready = true;
  visionReady = true;
  installed = true;
  packs = ['roundtable'];
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    const url = new URL(path, 'http://test');
    if (url.pathname === '/production/products') {
      if (init?.method === 'POST') {
        const saved = { ...JSON.parse(init.body as string), id: 'created-product' };
        products.push(saved);
        return saved;
      }
      return [...products];
    }
    if (url.pathname.startsWith('/production/products/') && !url.pathname.includes('/assets/')) {
      const id = url.pathname.split('/').at(-1);
      if (init?.method === 'DELETE') {
        products = products.filter((p) => p.id !== id);
        return { deleted: id };
      }
      const saved = { ...JSON.parse(init!.body as string), id };
      products = products.map((p) => (p.id === id ? saved : p));
      return saved;
    }
    if (url.pathname.includes('/assets/') && init?.method === 'PUT') {
      const id = url.pathname.split('/').at(-1);
      const patch = JSON.parse(init.body as string);
      assets = assets.map((a) => (a.id === id ? { ...a, ...patch } : a));
      return assets.find((a) => a.id === id);
    }
    if (
      url.pathname.startsWith('/production/products/') &&
      url.pathname.includes('/assets/') &&
      init?.method === 'POST'
    ) {
      const id = url.pathname.split('/').at(-1);
      const patch = JSON.parse(init.body as string);
      assets = assets.map((a) =>
        a.id === id
          ? {
              ...a,
              ...patch,
              product_id: url.pathname.split('/')[3],
              classification_status: 'pending',
            }
          : a,
      );
      return assets.find((a) => a.id === id);
    }
    if (url.pathname === '/production/assets') {
      if (url.searchParams.get('unassigned') === 'true') return assets.filter((a) => !a.product_id);
      if (init?.method === 'POST') {
        const asset = {
          ...one,
          id: 'upload-' + assets.length,
          filename: url.searchParams.get('filename'),
          product_id: url.searchParams.get('product_id'),
          video_type_id: url.searchParams.get('video_type_id'),
          category_id: null,
          classification_status: 'pending',
        };
        assets.push(asset);
        return asset;
      }
      return assets.filter((a) => a.product_id === url.searchParams.get('product_id'));
    }
    if (path === '/production/agent/status')
      return { planner: { ready }, vision: { ready: visionReady }, runtime: { installed, packs } };
    if (url.pathname === '/production/conversations') {
      if (init?.method === 'POST') {
        const created = {
          ...JSON.parse(init.body as string),
          id: 'new-chat',
          title: 'New discussion',
          messages: [],
          batches: [],
        };
        sessions.push(created);
        details[created.id] = created;
        return structuredClone(created);
      }
      return sessions.filter(
        (c) =>
          c.product_id === url.searchParams.get('product_id') &&
          c.video_type_id === url.searchParams.get('video_type_id'),
      );
    }
    if (url.pathname.startsWith('/production/conversations/')) {
      const id = url.pathname.split('/')[3];
      if (init?.method === 'POST') {
        const body = JSON.parse(init.body as string);
        details[id] = {
          ...details[id],
          messages: [
            ...details[id].messages,
            { id: body.request_id, role: 'user', text: body.text },
          ],
        };
      }
      return structuredClone(details[id]);
    }
    return {};
  });
});
it('hides existing-file assignment when no unassigned material or no product/type exists', async () => {
  mount();
  await readyToSend();
  await waitFor(() =>
    expect(client.isFetching({ queryKey: ['production-unassigned-assets'] })).toBe(0),
  );
  expect(screen.queryByRole('button', { name: key('importExisting') })).not.toBeInTheDocument();
  products[0].video_types = [];
  assets.push({ ...one, id: 'legacy', filename: 'Legacy.mp4', product_id: null });
  await act(async () => {
    await client.invalidateQueries({ queryKey: ['production-products'] });
    await client.invalidateQueries({ queryKey: ['production-unassigned-assets'] });
  });
  expect(screen.queryByRole('button', { name: key('importExisting') })).not.toBeInTheDocument();
});
it('previews and assigns multiple existing files to the current product/type without upload or AI', async () => {
  assets.push(
    { ...one, id: 'legacy-a', filename: 'Legacy A.mp4', product_id: null },
    { ...one, id: 'legacy-b', filename: 'Legacy B.mp4', product_id: null },
  );
  mount();
  await readyToSend();
  expect(screen.queryByRole('checkbox', { name: 'Legacy A.mp4' })).not.toBeInTheDocument();
  pick(key('videoTypes'), 'Voiceover');
  fireEvent.click(await screen.findByRole('button', { name: key('importExisting') }));
  fireEvent.click(screen.getByRole('button', { name: 'common.play Legacy A.mp4' }));
  expect(screen.getAllByRole('dialog').at(-1)?.querySelector('video')).toHaveAttribute(
    'src',
    '/production/assets/legacy-a/preview',
  );
  fireEvent.keyDown(document, { key: 'Escape', code: 'Escape' });
  await waitFor(() => expect(screen.getAllByRole('dialog')).toHaveLength(1));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Legacy A.mp4' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Legacy B.mp4' }));
  fireEvent.click(screen.getByRole('button', { name: key('assignToProduct') }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  const changes = request.mock.calls.filter(([, init]) => init?.method === 'POST');
  expect(changes.map(([path]) => path)).toEqual([
    '/production/products/product-a/assets/legacy-a',
    '/production/products/product-a/assets/legacy-b',
  ]);
  for (const [, init] of changes)
    expect(JSON.parse(init.body)).toEqual({ video_type_id: 'voiceover', role: 'input' });
  expect(screen.getByRole('checkbox', { name: 'Legacy A.mp4' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: key('importExisting') })).not.toBeInTheDocument();
});
it('starts with zero attachments and lets the backend select pending input without front-end keyword checks', async () => {
  mount();
  fireEvent.click(await readyToSend());
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(1));
  expect(lastBody('/production/conversations')).toEqual({
    product_id: product.id,
    video_type_id: 'showcase',
  });
  expect(lastBody('/production/conversations/new-chat/messages')).toEqual({
    text: 'production.batch.start',
    request_id: expect.any(String),
  });
  expect(
    request.mock.calls.some(
      ([path]) => path === '/production/brands' || path === '/production/batches',
    ),
  ).toBe(false);
});
it('allows chat without vision or configured categories and does not invoke classification on open or send', async () => {
  visionReady = false;
  products[0].categories = [];
  assets = [];
  mount();
  await readyToSend();
  expect(screen.getByText(key('visionHint'))).toBeInTheDocument();
  expect(screen.queryByText(key('setupHint'))).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: key('messagePlaceholder') }), {
    target: { value: 'Help me design material categories' },
  });
  fireEvent.click(screen.getByRole('button', { name: key('send') }));
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(1));
  expect(lastBody('/production/conversations/new-chat/messages')).not.toHaveProperty('source_ids');
  expect(request.mock.calls.some(([path]) => /classif/.test(path))).toBe(false);
});
it('sends an explicit empty attachment list for discussion and returns to selected inputs when a file is checked', async () => {
  mount();
  await readyToSend();
  fireEvent.click(screen.getByRole('checkbox', { name: one.filename }));
  fireEvent.click(screen.getByRole('checkbox', { name: key('discussionOnly') }));
  expect(screen.getByRole('checkbox', { name: one.filename })).not.toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'production.batch.start' }));
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(1));
  expect(lastBody('/production/conversations/new-chat/messages').source_ids).toEqual([]);
  await readyToSend();
  fireEvent.click(screen.getByRole('checkbox', { name: one.filename }));
  expect(screen.getByRole('checkbox', { name: key('discussionOnly') })).not.toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'production.batch.start' }));
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(2));
  expect(lastBody('/production/conversations/new-chat/messages').source_ids).toEqual(['one']);
});
it('shows a message immediately while the queue request is pending, then uses returned history', async () => {
  let resolve!: (value: any) => void;
  const original = request.getMockImplementation()!;
  request.mockImplementation((path, init) =>
    path.endsWith('/messages')
      ? new Promise((done) => {
          resolve = done;
        })
      : original(path, init),
  );
  mount();
  await readyToSend();
  fireEvent.change(screen.getByRole('textbox', { name: key('messagePlaceholder') }), {
    target: { value: 'Can we discuss an idea?' },
  });
  fireEvent.click(screen.getByRole('button', { name: key('send') }));
  expect(screen.getByText('Can we discuss an idea?')).toBeInTheDocument();
  await waitFor(() => expect(resolve).toBeDefined());
  expect(lastBody('/production/conversations/new-chat/messages')).not.toHaveProperty('source_ids');
  await act(async () =>
    resolve({
      ...details['new-chat'],
      messages: [{ id: 'accepted', role: 'user', text: 'Can we discuss an idea?' }],
    }),
  );
  await waitFor(() => expect(screen.getAllByText('Can we discuss an idea?')).toHaveLength(1));
});
it('attaches only explicitly selected files and clearing attachments returns to automatic input', async () => {
  mount();
  await readyToSend();
  fireEvent.click(screen.getByRole('checkbox', { name: one.filename }));
  fireEvent.click(screen.getByRole('button', { name: 'production.batch.start' }));
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(1));
  expect(lastBody('/production/conversations/new-chat/messages').source_ids).toEqual(['one']);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'production.batch.start' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('checkbox', { name: two.filename }));
  fireEvent.click(screen.getByRole('button', { name: 'common.clear' }));
  fireEvent.click(screen.getByRole('button', { name: 'production.batch.start' }));
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(2));
  expect(lastBody('/production/conversations/new-chat/messages')).not.toHaveProperty('source_ids');
});
it('uploads multiple videos to the selected product and narration type without media-kind filtering', async () => {
  mount();
  await readyToSend();
  pick(key('videoTypes'), 'Voiceover');
  const a = new File(['a'], 'A.mp4'),
    b = new File(['b'], 'B.mp4');
  fireEvent.change(screen.getByTestId('batch-upload'), { target: { files: [a, b] } });
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/production/assets?filename=B.mp4&product_id=product-a&video_type_id=voiceover',
      expect.objectContaining({ body: b }),
    ),
  );
  await waitFor(() => expect(screen.getByRole('checkbox', { name: 'A.mp4' })).toBeChecked());
  expect(screen.getByRole('checkbox', { name: one.filename })).toBeDisabled();
  fireEvent.click(await readyToSend());
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(1));
  expect(lastBody('/production/conversations/new-chat/messages').source_ids).toEqual([
    'upload-3',
    'upload-4',
  ]);
});
it('opens saved history and sends into the same conversation', async () => {
  sessions = [session];
  details[session.id] = session;
  mount();
  expect(await screen.findByText('Previous answer')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: key('messagePlaceholder') }), {
    target: { value: 'Continue with a different opening' },
  });
  fireEvent.click(screen.getByRole('button', { name: key('send') }));
  await waitFor(() => expect(posts('/production/conversations/chat-a/messages')).toHaveLength(1));
  expect(posts('/production/conversations')).toHaveLength(0);
  expect(lastBody('/production/conversations/chat-a/messages').text).toBe(
    'Continue with a different opening',
  );
});
it('groups delayed replies, tool logs and deliveries with their user turn before queued future turns', async () => {
  const first = {
    id: 'batch-first',
    status: 'done',
    items: [
      {
        source_id: 'one',
        filename: 'First delivery.mp4',
        status: 'done',
        artifacts: { video: 'first.mp4' },
      },
    ],
    log: [{ time: 1, kind: 'tool_result', text: 'First turn tool result' }],
  };
  const second = {
    id: 'batch-second',
    status: 'pending',
    items: [],
    log: [{ time: 2, kind: 'stage', text: 'Second turn queued' }],
  };
  sessions = [session];
  details[session.id] = {
    ...session,
    messages: [
      { id: 'first-user', role: 'user', text: 'First request', batch_id: first.id },
      { id: 'second-user', role: 'user', text: 'Queued next request', batch_id: second.id },
      {
        id: 'first-assistant',
        role: 'assistant',
        text: 'First request answer',
        batch_id: first.id,
      },
    ],
    // Rendering order follows user turns rather than the API batch array.
    batches: [second, first],
  };
  const { baseElement } = mount();
  await screen.findByText('First request answer');
  const turns = Array.from(baseElement.querySelectorAll<HTMLElement>('[data-turn-id]'));
  expect(turns.map((turn) => turn.dataset.batchId)).toEqual([first.id, second.id]);
  expect(within(turns[0]).getByText('First request')).toBeInTheDocument();
  expect(within(turns[0]).getByText('First request answer')).toBeInTheDocument();
  expect(turns[0]).toHaveTextContent('First turn tool result');
  expect(turns[0].querySelector('video')).toHaveAttribute(
    'src',
    '/production/batches/batch-first/items/one/files/video',
  );
  expect(turns[0]).not.toHaveTextContent('Queued next request');
  expect(turns[1]).toHaveTextContent('Queued next request');
  expect(turns[1]).toHaveTextContent('Second turn queued');
  expect(turns[1]).not.toHaveTextContent('First request answer');
});
it('localizes skipped batch and item states without an absent batch.status_skipped key', async () => {
  sessions = [session];
  details[session.id] = {
    ...session,
    messages: [
      { id: 'skipped-user', role: 'user', text: 'Discuss only', batch_id: 'skipped-batch' },
    ],
    batches: [
      {
        id: 'skipped-batch',
        status: 'skipped',
        log: [],
        items: [{ source_id: 'one', filename: one.filename, status: 'skipped', artifacts: {} }],
      },
    ],
  };
  mount();
  await screen.findByText('Discuss only');
  expect(screen.getByText(key('tools') + ' · ' + key('skipped'))).toBeInTheDocument();
  expect(screen.getByText(one.filename + ' · ' + key('skipped'))).toBeInTheDocument();
  expect(screen.queryByText(/batch\.status_skipped/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'production.batch.retryFailed' }),
  ).not.toBeInTheDocument();
});
it.each(['discuss', 'organize', 'produce'])(
  'shows activity rather than deliveries for a %s turn without artifacts and does not retry skipped input',
  async (turnAction) => {
    sessions = [session];
    details[session.id] = {
      ...session,
      messages: [
        { id: 'activity-user', role: 'user', text: 'Current turn', batch_id: 'activity-batch' },
      ],
      batches: [
        {
          id: 'activity-batch',
          status: 'done',
          request: { turn_action: turnAction },
          log: [{ time: 1, kind: 'tool_result', text: 'Discussion completed' }],
          items: [
            {
              source_id: 'one',
              filename: one.filename,
              status: 'skipped',
              error: 'No production was requested',
              artifacts: {},
            },
          ],
        },
      ],
    };
    mount();
    await screen.findByText('Current turn');
    expect(screen.getByText(key('tools') + ' · batch.status_done')).toBeInTheDocument();
    expect(screen.queryByText(/production\.deliveries/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /production.download/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'production.batch.retryFailed' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('No production was requested')).toHaveClass('text-muted-foreground');
  },
);
it('polls conversation progress and keeps assistant text separate from tool and runtime output', async () => {
  sessions = [session];
  details[session.id] = session;
  mount();
  await screen.findByText('Previous answer');
  const batch = {
    id: 'batch-a',
    status: 'running',
    items: [],
    log: [
      { kind: 'text', text: 'I am reviewing the scenes', time: 1 },
      { kind: 'tool_use', text: 'classify_materials', time: 2 },
      { kind: 'tool_result', text: 'Identified construction scenes', time: 3 },
      { kind: 'runtime', text: 'runtime diagnostic', time: 4 },
    ],
  };
  details[session.id] = { ...session, batches: [batch] };
  // Exercise the configured polling callback; this avoids a wall-clock wait in the suite.
  expect(
    client.getQueryCache().find({ queryKey: ['production-conversation', 'chat-a'] })?.options,
  ).toMatchObject({ refetchInterval: 1500 });
  await act(async () => {
    await client.refetchQueries({ queryKey: ['production-conversation', 'chat-a'] });
  });
  expect(await screen.findByText('I am reviewing the scenes')).toHaveAttribute(
    'class',
    expect.stringContaining('whitespace-pre-wrap'),
  );
  expect(screen.getByText('I am reviewing the scenes').closest('article')).toHaveAttribute(
    'data-message-role',
    'assistant',
  );
  const tools = screen.getByRole('log', { name: key('tools'), hidden: true });
  expect(tools).toHaveTextContent('classify_materials');
  expect(tools).toHaveTextContent('runtime diagnostic');
  expect(screen.getByText(/runtime diagnostic/).closest('[data-message-role]')).toBeNull();
});
it('retries a failed message with its original request ID and avoids creating another session', async () => {
  const original = request.getMockImplementation()!;
  let fail = true;
  request.mockImplementation((path, init) => {
    if (path.endsWith('/messages') && fail) {
      fail = false;
      return Promise.reject(new Error('Network lost'));
    }
    return original(path, init);
  });
  mount();
  fireEvent.click(await readyToSend());
  fireEvent.click(await screen.findByRole('button', { name: key('retrySend') }));
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(2));
  const attempts = posts('/production/conversations/new-chat/messages').map(([, init]) =>
    JSON.parse(init.body),
  );
  expect(attempts[0]).toEqual(attempts[1]);
  expect(posts('/production/conversations')).toHaveLength(1);
});
it('resets history and attachments on product or production-type changes', async () => {
  products.push({
    ...structuredClone(product),
    id: 'product-b',
    name: 'Furniture',
    categories: [],
  });
  sessions = [session];
  details[session.id] = session;
  assets.push({ ...one, id: 'foreign', product_id: 'product-b', filename: 'Furniture.mp4' });
  mount();
  await screen.findByText('Previous answer');
  fireEvent.click(screen.getByRole('checkbox', { name: one.filename }));
  pick(key('products'), 'Furniture');
  await screen.findByRole('checkbox', { name: 'Furniture.mp4' });
  expect(screen.queryByText('Previous answer')).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: one.filename })).not.toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: key('removeAttachment') + ' ' + one.filename }),
  ).not.toBeInTheDocument();
  pick(key('videoTypes'), 'Voiceover');
  expect(screen.getByRole('checkbox', { name: 'Furniture.mp4' })).toBeDisabled();
});
it('shows media by category and saves independent category, type and role assignments', async () => {
  mount();
  await readyToSend();
  pick(key('category') + ' ' + two.filename, 'Construction');
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/production/products/product-a/assets/two',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ category_id: 'construction' }),
      }),
    ),
  );
  await readyToSend();
  pick(key('role') + ' ' + two.filename, key('roleMaterial'));
  await waitFor(() => expect(screen.getByRole('checkbox', { name: two.filename })).toBeDisabled());
  await readyToSend();
  pick(key('videoTypes') + ' ' + two.filename, 'Voiceover');
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/production/products/product-a/assets/two',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ video_type_id: 'voiceover' }),
      }),
    ),
  );
  await readyToSend();
  pick(key('categories'), 'Construction');
  expect(screen.getByRole('checkbox', { name: one.filename })).toBeInTheDocument();
});
it('creates products with no hardcoded categories, three editable default types, and the existing voice selector', async () => {
  mount();
  await readyToSend();
  fireEvent.click(screen.getByRole('button', { name: key('newProduct') }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('Native voice selector')).toBeInTheDocument();
  expect(
    within(dialog).queryByRole('textbox', { name: key('categoryName') + ' 1' }),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).getAllByRole('textbox', { name: new RegExp(key('videoTypeName')) }),
  ).toHaveLength(3);
  fireEvent.change(within(dialog).getByRole('textbox', { name: key('productName') }), {
    target: { value: 'New product' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: key('addCategory') }));
  fireEvent.change(within(dialog).getByRole('textbox', { name: key('categoryName') + ' 1' }), {
    target: { value: 'Custom scenes' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: key('addVideoType') }));
  fireEvent.change(within(dialog).getByRole('textbox', { name: key('videoTypeName') + ' 4' }), {
    target: { value: 'Case study' },
  });
  pick(key('workflow') + ' 4', key('workflowNarration'));
  fireEvent.click(within(dialog).getByRole('button', { name: 'common.save' }));
  await waitFor(() => expect(posts('/production/products')).toHaveLength(1));
  const saved = lastBody('/production/products');
  expect(saved).not.toHaveProperty('id');
  expect(saved.categories).toEqual([
    { id: expect.any(String), name: 'Custom scenes', description: '' },
  ]);
  expect(saved.video_types.map((v: any) => v.workflow)).toEqual([
    'product',
    'talking_head',
    'narration',
    'narration',
  ]);
});

it('keeps the chosen session stable when the conversation list is reordered and opens a fresh chat explicitly', async () => {
  sessions = [session];
  details[session.id] = session;
  mount();
  await screen.findByText('Previous answer');
  const newer = { ...session, id: 'newer-chat', title: 'Another discussion', messages: [] };
  sessions = [newer, session];
  details[newer.id] = newer;
  await act(async () => {
    await client.refetchQueries({ queryKey: ['production-conversations'] });
  });
  expect(screen.getByText('Previous answer')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: session.title })).toHaveAttribute(
    'aria-current',
    'page',
  );
  fireEvent.click(screen.getByRole('button', { name: key('newConversation') }));
  expect(screen.queryByText('Previous answer')).not.toBeInTheDocument();
  fireEvent.click(await readyToSend());
  await waitFor(() => expect(posts('/production/conversations/new-chat/messages')).toHaveLength(1));
  expect(posts('/production/conversations')).toHaveLength(1);
});
it('edits and removes category/type configuration and deletes a product only after confirmation', async () => {
  mount();
  await readyToSend();
  fireEvent.click(screen.getByRole('button', { name: key('editProduct') }));
  let dialog = screen.getByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: key('removeCategory') }));
  fireEvent.click(within(dialog).getAllByRole('button', { name: key('removeVideoType') })[1]);
  fireEvent.click(within(dialog).getByRole('button', { name: 'common.save' }));
  await waitFor(() =>
    expect(
      request.mock.calls.some(
        ([p, i]) => p === '/production/products/product-a' && i?.method === 'PUT',
      ),
    ).toBe(true),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: key('editProduct') }));
  dialog = screen.getByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: key('deleteProduct') }));
  expect(request.mock.calls.some(([, i]) => i?.method === 'DELETE')).toBe(false);
  expect(within(dialog).getByRole('alert')).toHaveTextContent(key('deleteConfirm'));
  fireEvent.click(within(dialog).getByRole('button', { name: key('deleteProduct') }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith('/production/products/product-a', { method: 'DELETE' }),
  );
});
it('preserves runtime installation, pack import and setup reminders without invoking AI on open', async () => {
  ready = false;
  installed = false;
  packs = [];
  mount();
  await screen.findByText(key('setupHint'));
  expect(await screen.findByRole('button', { name: 'production.batch.start' })).toBeDisabled();
  expect(request.mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(0);
  fireEvent.click(await screen.findByRole('button', { name: 'production.batch.installRuntime' }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith('/production/runtime/install', { method: 'POST' }),
  );
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'production.batch.importPack' })).toBeEnabled(),
  );
  const zip = new File(['zip'], 'workflow.zip');
  fireEvent.change(screen.getByTestId('pack-upload'), { target: { files: [zip] } });
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/production/packs/roundtable',
      expect.objectContaining({ body: zip }),
    ),
  );
});
it('previews actual media and keeps mixed batch output previews, downloads and failed-only retries', async () => {
  const batch = {
    id: 'mixed',
    status: 'done',
    items: [
      {
        source_id: 'one',
        filename: one.filename,
        status: 'done',
        artifacts: { video: 'video.mp4', cover: 'cover.jpg', subtitles: 'captions.srt' },
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
  };
  sessions = [session];
  details[session.id] = { ...session, batches: [batch] };
  const { baseElement } = mount();
  await screen.findByText('Missing useful footage');
  expect(screen.getByRole('link', { name: 'production.download' })).toHaveAttribute(
    'href',
    '/production/batches/mixed/download',
  );
  expect(screen.getByRole('link', { name: 'production.download · subtitles' })).toHaveAttribute(
    'href',
    '/production/batches/mixed/items/one/files/subtitles',
  );
  expect(baseElement.querySelector('video')).toHaveAttribute(
    'src',
    '/production/batches/mixed/items/one/files/video',
  );
  fireEvent.click(screen.getByRole('button', { name: 'production.batch.retryFailed' }));
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith('/production/batches/mixed/retry', { method: 'POST' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'common.play First.mp4' }));
  expect(within(screen.getByRole('dialog')).getByText(one.filename)).toBeInTheDocument();
  expect(screen.getByRole('dialog').querySelector('video')).toHaveAttribute(
    'src',
    '/production/assets/one/preview',
  );
});
