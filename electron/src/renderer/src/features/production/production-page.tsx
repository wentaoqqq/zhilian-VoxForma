import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ClapperboardIcon, UploadIcon, PlusIcon, SendIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
// @ts-expect-error Existing shared JSX controls have no declaration files.
import SearchableSelect from '@shared/components/SearchableSelect';
// @ts-expect-error Reuse Studio's native voice selector.
import VoiceSelector from '@shared/components/VoiceSelector';
import { useProfiles } from '@/hooks/use-profiles';
import { apiJson, apiPath, describeError } from '@/lib/api/client';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';

type Workflow = 'product' | 'talking_head' | 'narration';
type Role = 'input' | 'material' | 'outro' | 'sticker';
interface Category {
  id: string;
  name: string;
  description: string;
}
interface VideoType {
  id: string;
  name: string;
  workflow: Workflow;
  instruction: string;
}
interface Product {
  id: string;
  name: string;
  notes: string;
  pack_id: string;
  outro_ids: string[];
  material_ids: string[];
  sticker_ids: string[];
  voice_profile: string | null;
  tts_engine: string | null;
  categories: Category[];
  video_types: VideoType[];
}
interface Asset {
  id: string;
  filename: string;
  kind?: 'video' | 'audio' | 'image' | 'text';
  product_id: string;
  category_id?: string | null;
  video_type_id?: string | null;
  classification_status?: string;
  role?: Role;
}
interface Batch {
  id: string;
  status: string;
  error?: string;
  items: {
    source_id: string;
    filename: string;
    status: string;
    error?: string;
    artifacts: Record<string, string>;
  }[];
  log: { time: number; kind: string; text: string }[];
}
interface Message {
  id: string;
  role: string;
  text: string;
  batch_id?: string;
  created_at?: number | string;
}
interface Conversation {
  id: string;
  product_id: string;
  video_type_id: string;
  title?: string;
  messages: Message[];
  batches: Batch[];
}
interface Turn {
  id: string;
  messages: Message[];
  batch?: Batch;
}
interface AgentStatus {
  planner: { ready: boolean };
  vision: { ready: boolean };
  runtime: { installed: boolean; packs: string[]; job?: { status: string; error?: string } | null };
}
interface Outgoing {
  productId: string;
  typeId: string;
  conversationId: string;
  text: string;
  sourceIds?: string[];
  requestId: string;
  error?: string;
}
const base = '/production';
const picker = 'w-full rounded-md border border-border bg-background px-3 py-2 text-left text-sm';
const workflows: Workflow[] = ['product', 'talking_head', 'narration'];
const workflowKeys = {
  product: 'workflowProduct',
  talking_head: 'workflowTalkingHead',
  narration: 'workflowNarration',
};
const roles: Role[] = ['input', 'material', 'outro', 'sticker'];
const roleKeys = {
  input: 'roleInput',
  material: 'roleMaterial',
  outro: 'roleOutro',
  sticker: 'roleSticker',
};
const classificationKeys: Record<string, string> = {
  pending: 'classificationPending',
  classified: 'classificationClassified',
  unclassified: 'classificationUnclassified',
};
// HTTP LAN deployments do not expose randomUUID in every browser.
const newId = () =>
  globalThis.crypto?.randomUUID?.() ??
  Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
function rememberedProduct() {
  try {
    return localStorage.getItem('voxforma.production.product') || '';
  } catch {
    return '';
  }
}

export function ProductionPage() {
  const { t } = useTranslation();
  const w = (key: string, values?: Record<string, unknown>) =>
    t('production.workspace.' + key, values);
  const client = useQueryClient();
  const profiles = useProfiles();
  const [productId, setProductId] = useState(rememberedProduct);
  const [typeId, setTypeId] = useState('');
  // null opens the latest saved session; an empty string explicitly starts a new one.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [discussionOnly, setDiscussionOnly] = useState(false);
  const [categoryId, setCategoryId] = useState('');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState('');
  const [editor, setEditor] = useState<Product | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [preview, setPreview] = useState<Asset | null>(null);
  const [outgoing, setOutgoing] = useState<Outgoing | null>(null);
  const sending = useRef(false);
  const uploadRef = useRef<HTMLInputElement>(null);
  const packRef = useRef<HTMLInputElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const products = useQuery({
    queryKey: ['production-products'],
    queryFn: () => apiJson<Product[]>(base + '/products'),
  });
  const product = products.data?.find((p) => p.id === productId) ?? products.data?.[0];
  const videoType = product?.video_types.find((v) => v.id === typeId) ?? product?.video_types[0];
  const scope = new URLSearchParams({
    product_id: product?.id ?? '',
    video_type_id: videoType?.id ?? '',
  });
  const assets = useQuery({
    queryKey: ['production-assets', product?.id],
    enabled: !!product,
    queryFn: () =>
      apiJson<Asset[]>(base + '/assets?' + new URLSearchParams({ product_id: product!.id })),
    refetchInterval: 3000,
  });
  const conversations = useQuery({
    queryKey: ['production-conversations', product?.id, videoType?.id],
    enabled: !!product && !!videoType,
    queryFn: () => apiJson<Conversation[]>(base + '/conversations?' + scope),
    refetchInterval: 3000,
  });
  const activeId = conversationId === null ? (conversations.data?.[0]?.id ?? '') : conversationId;
  useEffect(() => {
    if (conversationId === null && conversations.data)
      setConversationId(conversations.data[0]?.id ?? '');
  }, [conversationId, conversations.data]);
  const conversation = useQuery({
    queryKey: ['production-conversation', activeId],
    enabled: !!activeId,
    queryFn: () => apiJson<Conversation>(base + '/conversations/' + activeId),
    refetchInterval: 1500,
  });
  const status = useQuery({
    queryKey: ['production-agent-status'],
    queryFn: () => apiJson<AgentStatus>(base + '/agent/status'),
    refetchInterval: 10000,
  });
  const ready = !!(
    status.data?.planner.ready &&
    status.data.runtime.installed &&
    product &&
    status.data.runtime.packs.includes(product.pack_id)
  );
  const canSend = ready && !!videoType && !conversations.isPending;
  const scopedAssets = (assets.data ?? []).filter((a) => a.product_id === product?.id);
  const roleOf = (a: Asset): Role =>
    a.role ??
    (product?.outro_ids.includes(a.id)
      ? 'outro'
      : product?.sticker_ids.includes(a.id)
        ? 'sticker'
        : product?.material_ids.includes(a.id)
          ? 'material'
          : 'input');
  const attachments = scopedAssets.filter(
    (a) => selected.includes(a.id) && a.video_type_id === videoType?.id && roleOf(a) === 'input',
  );
  const batches = conversation.data?.batches ?? [];
  const visibleOutgoing =
    outgoing &&
    outgoing.productId === product?.id &&
    outgoing.typeId === videoType?.id &&
    outgoing.conversationId === activeId
      ? outgoing
      : null;
  const messages = conversation.data?.messages ?? [];
  const batchesById = new Map(batches.map((batch) => [batch.id, batch]));
  const turns: Turn[] = [];
  const turnsByBatch = new Map<string, Turn>();
  let latestUserTurn: Turn | undefined;
  for (const message of messages) {
    let turn = message.batch_id ? turnsByBatch.get(message.batch_id) : undefined;
    if (!turn && !message.batch_id && message.role !== 'user') turn = latestUserTurn;
    if (!turn) {
      turn = {
        id: message.id,
        messages: [],
        batch: message.batch_id ? batchesById.get(message.batch_id) : undefined,
      };
      turns.push(turn);
    }
    turn.messages.push(message);
    if (message.batch_id) turnsByBatch.set(message.batch_id, turn);
    if (message.role === 'user') latestUserTurn = turn;
  }
  // Preserve batches from older conversations whose messages have no batch_id.
  for (const batch of batches) {
    if (!turnsByBatch.has(batch.id)) turns.push({ id: 'batch-' + batch.id, messages: [], batch });
  }
  const progressSize = batches.reduce((n, b) => n + (b.log?.length ?? 0), 0);
  useEffect(() => {
    transcriptRef.current?.scrollTo?.({ top: transcriptRef.current.scrollHeight });
  }, [activeId, messages.length, progressSize, outgoing]);
  async function refresh() {
    await Promise.all(
      [
        'production-products',
        'production-assets',
        'production-conversations',
        'production-conversation',
        'production-agent-status',
      ].map((key) => client.invalidateQueries({ queryKey: [key] })),
    );
  }
  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await fn();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
      setUploading('');
    }
  }
  function resetChat() {
    setConversationId(null);
    setSelected([]);
    setDiscussionOnly(false);
    setDraft('');
    setOutgoing(null);
    setError('');
  }
  function chooseProduct(id: string) {
    setProductId(id);
    setTypeId('');
    setCategoryId('');
    resetChat();
    try {
      localStorage.setItem('voxforma.production.product', id);
    } catch {
      /* In-memory selection works. */
    }
  }
  function createProduct() {
    setDeleting(false);
    setEditor({
      id: '',
      name: '',
      notes: '',
      pack_id: 'roundtable',
      outro_ids: [],
      material_ids: [],
      sticker_ids: [],
      voice_profile: null,
      tts_engine: null,
      categories: [],
      video_types: workflows.map((workflow) => ({
        id: newId(),
        name: w(workflowKeys[workflow]),
        workflow,
        instruction: '',
      })),
    });
  }
  async function saveProduct() {
    if (!editor) return;
    await action(async () => {
      const { id, ...payload } = editor;
      const saved = await apiJson<Product>(base + '/products' + (id ? '/' + id : ''), {
        method: id ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      });
      chooseProduct(saved.id);
      setEditor(null);
      await refresh();
    });
  }
  async function upload(files: File[]) {
    if (!product || !videoType || !files.length) return;
    const uploadProduct = product.id,
      uploadType = videoType.id;
    await action(async () => {
      const ids: string[] = [];
      try {
        for (const file of files) {
          setUploading(file.name);
          const asset = await apiJson<Asset>(
            base +
              '/assets?' +
              new URLSearchParams({
                filename: file.name,
                product_id: uploadProduct,
                video_type_id: uploadType,
              }),
            { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } },
          );
          ids.push(asset.id);
        }
      } finally {
        if (ids.length) setDiscussionOnly(false);
        setSelected((current) => [...new Set([...current, ...ids])]);
        await client.invalidateQueries({ queryKey: ['production-assets', uploadProduct] });
      }
    });
  }
  async function updateAsset(
    asset: Asset,
    patch: { category_id?: string | null; video_type_id?: string; role?: Role },
  ) {
    if (!product) return;
    await action(async () => {
      await apiJson(base + '/products/' + product.id + '/assets/' + asset.id, {
        method: 'PUT',
        body: JSON.stringify(patch),
      });
      await refresh();
    });
  }
  async function send(retry?: Outgoing) {
    if (!product || !videoType || sending.current) return;
    sending.current = true;
    const next = retry ?? {
      productId: product.id,
      typeId: videoType.id,
      conversationId: activeId,
      text: draft.trim() || t('production.batch.start'),
      sourceIds: discussionOnly
        ? []
        : attachments.length
          ? attachments.map((a) => a.id)
          : undefined,
      requestId: newId(),
    };
    setOutgoing({ ...next, error: undefined });
    setDraft('');
    setBusy(true);
    setError('');
    try {
      if (!next.conversationId) {
        const created = await apiJson<Conversation>(base + '/conversations', {
          method: 'POST',
          body: JSON.stringify({ product_id: next.productId, video_type_id: next.typeId }),
        });
        next.conversationId = created.id;
        client.setQueryData(['production-conversation', created.id], created);
        setConversationId(created.id);
        setOutgoing({ ...next, error: undefined });
      }
      await client.cancelQueries({ queryKey: ['production-conversation', next.conversationId] });
      const result = await apiJson<Conversation>(
        base + '/conversations/' + next.conversationId + '/messages',
        {
          method: 'POST',
          body: JSON.stringify({
            text: next.text,
            ...(next.sourceIds !== undefined ? { source_ids: next.sourceIds } : {}),
            request_id: next.requestId,
          }),
        },
      );
      client.setQueryData(['production-conversation', result.id], result);
      setOutgoing(null);
      setSelected([]);
      await client.invalidateQueries({
        queryKey: ['production-conversations', next.productId, next.typeId],
      });
    } catch (e) {
      setOutgoing({ ...next, error: describeError(e) });
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }
  const state = (value: string) =>
    value === 'skipped'
      ? w('skipped')
      : t('batch.status_' + (value === 'pending' ? 'queued' : value));
  const fileUrl = (batch: Batch, source: string, name: string) =>
    apiPath(
      base + '/batches/' + batch.id + '/items/' + source + '/files/' + encodeURIComponent(name),
    );
  const selectProps = { buttonClassName: picker, menuPortal: true, disabled: busy };
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="flex items-center gap-2">
          <ClapperboardIcon className="size-4" />
          {t('production.title')}
        </h1>
      </WorkspaceHeader>
      <main
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4"
        data-testid="production-page"
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid min-w-48 flex-1 gap-1">
            <span className="text-sm">{w('products')}</span>
            <SearchableSelect
              {...selectProps}
              value={product?.id ?? ''}
              onChange={chooseProduct}
              options={(products.data ?? []).map((p) => ({ value: p.id, label: p.name }))}
              ariaLabel={w('products')}
              placeholder={w('chooseProduct')}
            />
          </div>
          <div className="grid min-w-48 flex-1 gap-1">
            <span className="text-sm">{w('videoTypes')}</span>
            <SearchableSelect
              {...selectProps}
              value={videoType?.id ?? ''}
              onChange={(id: string) => {
                setTypeId(id);
                resetChat();
              }}
              options={(product?.video_types ?? []).map((v) => ({ value: v.id, label: v.name }))}
              ariaLabel={w('videoTypes')}
              placeholder={w('chooseVideoType')}
            />
          </div>
          <Button variant="outline" disabled={busy} onClick={createProduct}>
            <PlusIcon />
            {w('newProduct')}
          </Button>
          <Button
            variant="outline"
            disabled={busy || !product}
            onClick={() => {
              setDeleting(false);
              setEditor(structuredClone(product!));
            }}
          >
            {w('editProduct')}
          </Button>
        </div>
        {(error ||
          products.error ||
          assets.error ||
          conversations.error ||
          conversation.error ||
          status.error) && (
          <p role="alert" className="break-words text-sm text-destructive">
            {error ||
              describeError(
                products.error ||
                  assets.error ||
                  conversations.error ||
                  conversation.error ||
                  status.error,
              )}
          </p>
        )}
        {busy && (
          <p role="status" className="text-sm">
            {t('common.saving')} {uploading}
          </p>
        )}
        {(!ready || (status.data && !status.data.vision.ready)) && (
          <section className="grid gap-2 rounded-lg border border-border p-3 text-sm">
            {!ready && <p>{w('setupHint')}</p>}
            {status.data && !status.data.vision.ready && <p>{w('visionHint')}</p>}
            {status.data?.runtime.job?.error && <p role="alert">{status.data.runtime.job.error}</p>}
            <div className="flex flex-wrap items-center gap-3">
              <a href="#/settings/models/llm" className="underline">
                {t('production.ai.settings')}
              </a>
              {status.data && !status.data.runtime.installed && (
                <Button
                  variant="outline"
                  disabled={
                    busy || ['pending', 'running'].includes(status.data.runtime.job?.status ?? '')
                  }
                  onClick={() =>
                    void action(async () => {
                      await apiJson(base + '/runtime/install', { method: 'POST' });
                      await refresh();
                    })
                  }
                >
                  {t('production.batch.installRuntime')}
                </Button>
              )}
              {product && status.data && !status.data.runtime.packs.includes(product.pack_id) && (
                <Button variant="outline" disabled={busy} onClick={() => packRef.current?.click()}>
                  {t('production.batch.importPack')}
                </Button>
              )}
            </div>
          </section>
        )}
        <input
          ref={packRef}
          data-testid="pack-upload"
          type="file"
          accept=".zip"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          disabled={busy || !product}
          onChange={(e) => {
            const file = e.currentTarget.files?.[0];
            e.currentTarget.value = '';
            if (file && product)
              void action(async () => {
                await apiJson(base + '/packs/' + encodeURIComponent(product.pack_id), {
                  method: 'POST',
                  body: file,
                  headers: { 'Content-Type': 'application/zip' },
                });
                await refresh();
              });
          }}
        />
        <input
          ref={uploadRef}
          data-testid="batch-upload"
          type="file"
          multiple
          accept=".mp4,.mov,.mkv,.webm,.avi,.m4v,.wav,.mp3,.m4a,.flac,.ogg,.png,.jpg,.jpeg,.webp,.txt"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          disabled={busy || !product || !videoType}
          onChange={(e) => {
            const files = Array.from(e.currentTarget.files ?? []);
            e.currentTarget.value = '';
            void upload(files);
          }}
        />
        <div className="grid min-h-[32rem] flex-1 gap-4 lg:min-h-0 lg:grid-cols-[12rem_minmax(0,1fr)_20rem]">
          <aside
            className="flex min-h-0 flex-col gap-3 rounded-xl border border-border p-3"
            aria-label={w('conversations')}
          >
            <h2 className="font-medium">{w('conversations')}</h2>
            <Button
              variant="outline"
              disabled={busy || !product || !videoType}
              onClick={() => {
                setConversationId('');
                setDraft('');
                setOutgoing(null);
                setSelected([]);
                setDiscussionOnly(false);
              }}
            >
              {w('newConversation')}
            </Button>
            <div className="grid gap-2 overflow-auto">
              {!conversations.data?.length && (
                <p className="text-sm text-muted-foreground">{w('noConversations')}</p>
              )}
              {(conversations.data ?? []).map((c) => (
                <button
                  key={c.id}
                  type="button"
                  aria-current={c.id === activeId ? 'page' : undefined}
                  disabled={busy}
                  className={
                    'break-words rounded-lg p-2 text-left text-sm ' +
                    (c.id === activeId ? 'bg-muted' : 'hover:bg-muted/50')
                  }
                  onClick={() => {
                    setConversationId(c.id);
                    setOutgoing(null);
                    setDraft('');
                    setSelected([]);
                    setDiscussionOnly(false);
                  }}
                >
                  {c.title || w('untitledConversation')}
                </button>
              ))}
            </div>
          </aside>
          <section
            className="flex min-h-0 flex-col rounded-xl border border-border"
            aria-label={w('assistant')}
          >
            <div
              ref={transcriptRef}
              className="flex-1 space-y-4 overflow-y-auto p-4"
              role="log"
              aria-label={w('conversations')}
            >
              {!messages.length && !visibleOutgoing && (
                <p className="py-10 text-center text-sm text-muted-foreground">{w('chatEmpty')}</p>
              )}
              {turns.map((turn) => (
                <section
                  key={turn.id}
                  data-turn-id={turn.id}
                  data-batch-id={turn.batch?.id}
                  className="space-y-4"
                >
                  {turn.messages.map((m) => (
                    <article
                      key={m.id}
                      data-message-role={m.role}
                      className={
                        'max-w-[95%] rounded-xl p-3 text-sm ' +
                        (m.role === 'user' ? 'ml-auto bg-primary/10' : 'bg-muted/40')
                      }
                    >
                      <p className="mb-1 text-xs font-medium text-muted-foreground">
                        {w(
                          m.role === 'user'
                            ? 'you'
                            : m.role === 'assistant'
                              ? 'assistant'
                              : 'system',
                        )}
                      </p>
                      <p className="whitespace-pre-wrap break-words">{m.text}</p>
                    </article>
                  ))}
                  {turn.batch &&
                    (() => {
                      const batch = turn.batch;
                      const active = ['pending', 'running'].includes(batch.status);
                      const hasDeliveries = batch.items.some(
                        (item) =>
                          item.status === 'done' && Object.values(item.artifacts).some(Boolean),
                      );
                      const assistantEvents = (batch.log ?? []).filter((event) =>
                        ['text', 'assistant'].includes(event.kind),
                      );
                      const toolEvents = (batch.log ?? []).filter(
                        (event) => !['text', 'assistant'].includes(event.kind),
                      );
                      // OpenCode emits text as assistant progress. Tool/runtime events remain an activity log.
                      const hasAssistantMessage = turn.messages.some(
                        (m) => m.role === 'assistant' && m.batch_id === batch.id,
                      );
                      return (
                        <section
                          key={batch.id}
                          aria-label={batch.id}
                          className="space-y-3 rounded-xl border border-border p-3"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                            <span>
                              {hasDeliveries ? t('production.deliveries') : w('tools')} ·{' '}
                              {state(batch.status)}
                            </span>
                            <span className="text-xs text-muted-foreground">{batch.id}</span>
                          </div>
                          {!hasAssistantMessage &&
                            assistantEvents.map((event, index) => (
                              <article
                                key={index}
                                data-message-role="assistant"
                                className="rounded-lg bg-muted/40 p-3 text-sm"
                              >
                                <p className="mb-1 text-xs font-medium">{w('assistant')}</p>
                                <p className="whitespace-pre-wrap break-words">{event.text}</p>
                              </article>
                            ))}
                          {batch.error && (
                            <p
                              role={batch.status === 'skipped' ? 'status' : 'alert'}
                              className={
                                'break-words text-sm ' +
                                (batch.status === 'skipped'
                                  ? 'text-muted-foreground'
                                  : 'text-destructive')
                              }
                            >
                              {batch.error}
                            </p>
                          )}
                          <details>
                            <summary className="cursor-pointer text-sm">
                              {w('tools')} · {toolEvents.length}
                            </summary>
                            <div
                              role="log"
                              aria-label={w('tools')}
                              className="mt-2 max-h-72 space-y-2 overflow-auto rounded bg-muted/40 p-2"
                            >
                              {toolEvents.map((event, index) => (
                                <p
                                  key={index}
                                  className="whitespace-pre-wrap break-words font-mono text-xs"
                                >
                                  [{event.kind}] {event.text}
                                </p>
                              ))}
                            </div>
                          </details>
                          {(batch.items ?? []).map((item) => (
                            <div
                              key={item.source_id}
                              className="space-y-2 rounded-lg border border-border p-2 text-sm"
                            >
                              <p className="break-words">
                                {item.filename} ·{' '}
                                {state(
                                  !active && item.status === 'pending' ? batch.status : item.status,
                                )}
                              </p>
                              {item.error && (
                                <p
                                  className={
                                    'break-words ' +
                                    (item.status === 'skipped'
                                      ? 'text-muted-foreground'
                                      : 'text-destructive')
                                  }
                                >
                                  {item.error}
                                </p>
                              )}
                              {item.status === 'done' && (
                                <>
                                  {item.artifacts.video && (
                                    <video
                                      src={fileUrl(batch, item.source_id, 'video')}
                                      controls
                                      preload="metadata"
                                      playsInline
                                      className="max-h-80 w-full rounded bg-black"
                                    />
                                  )}
                                  {item.artifacts.cover && (
                                    <img
                                      src={fileUrl(batch, item.source_id, 'cover')}
                                      alt={item.filename}
                                      loading="lazy"
                                      className="max-h-60 max-w-full rounded object-contain"
                                    />
                                  )}
                                  <div className="flex flex-wrap gap-3">
                                    {Object.keys(item.artifacts).map((name) => (
                                      <a
                                        key={name}
                                        className="underline"
                                        href={fileUrl(batch, item.source_id, name)}
                                        download
                                      >
                                        {t('production.download')} · {name}
                                      </a>
                                    ))}
                                  </div>
                                </>
                              )}
                            </div>
                          ))}
                          <div className="flex flex-wrap gap-2">
                            {active ? (
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={busy}
                                onClick={() =>
                                  void action(async () => {
                                    await apiJson(base + '/batches/' + batch.id + '/cancel', {
                                      method: 'POST',
                                    });
                                    await refresh();
                                  })
                                }
                              >
                                {t('common.cancel')}
                              </Button>
                            ) : batch.status !== 'skipped' &&
                              batch.items.some((i) => !['done', 'skipped'].includes(i.status)) ? (
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={busy}
                                onClick={() =>
                                  void action(async () => {
                                    await apiJson(base + '/batches/' + batch.id + '/retry', {
                                      method: 'POST',
                                    });
                                    await refresh();
                                  })
                                }
                              >
                                {t('production.batch.retryFailed')}
                              </Button>
                            ) : null}
                            {hasDeliveries && (
                              <a
                                className="text-sm underline"
                                href={apiPath(base + '/batches/' + batch.id + '/download')}
                              >
                                {t('production.download')}
                              </a>
                            )}
                          </div>
                        </section>
                      );
                    })()}
                </section>
              ))}
              {visibleOutgoing && (
                <article
                  data-message-role="user"
                  className="ml-auto max-w-[95%] rounded-xl bg-primary/10 p-3 text-sm"
                >
                  <p className="mb-1 text-xs text-muted-foreground">
                    {w('you')} · {visibleOutgoing.error ? w('sendFailed') : t('common.saving')}
                  </p>
                  <p className="whitespace-pre-wrap break-words">{visibleOutgoing.text}</p>
                  {visibleOutgoing.error && (
                    <>
                      <p role="alert" className="mt-2 text-destructive">
                        {visibleOutgoing.error}
                      </p>
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => void send(visibleOutgoing)}
                      >
                        {w('retrySend')}
                      </Button>
                    </>
                  )}
                </article>
              )}
            </div>
            <form
              className="space-y-3 border-t border-border p-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!busy && canSend && !visibleOutgoing?.error) void send();
              }}
            >
              <p className="text-xs text-muted-foreground">{w('autoInputs')}</p>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={discussionOnly}
                  disabled={busy || !!visibleOutgoing?.error}
                  onChange={(e) => {
                    setDiscussionOnly(e.target.checked);
                    if (e.target.checked) setSelected([]);
                  }}
                />
                {w('discussionOnly')}
              </label>
              {attachments.length > 0 && (
                <div aria-label={w('attachments')} className="flex flex-wrap gap-2">
                  {attachments.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      disabled={busy}
                      aria-label={w('removeAttachment') + ' ' + a.filename}
                      className="max-w-full truncate rounded border border-border px-2 py-1 text-xs"
                      onClick={() => setSelected((ids) => ids.filter((id) => id !== a.id))}
                    >
                      {a.filename} ×
                    </button>
                  ))}
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setSelected([])}
                  >
                    {t('common.clear')}
                  </Button>
                </div>
              )}
              <Textarea
                rows={3}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={busy || !!visibleOutgoing?.error}
                placeholder={w('messagePlaceholder')}
                aria-label={w('messagePlaceholder')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    e.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              <div className="flex justify-between gap-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy || !product || !videoType}
                  onClick={() => uploadRef.current?.click()}
                >
                  <UploadIcon />
                  {t('production.batch.upload')}
                </Button>
                <Button type="submit" disabled={busy || !canSend || !!visibleOutgoing?.error}>
                  <SendIcon />
                  {draft.trim() ? w('send') : t('production.batch.start')}
                </Button>
              </div>
            </form>
          </section>
          <aside
            className="flex min-h-0 flex-col gap-3 rounded-xl border border-border p-3"
            aria-label={t('production.library')}
          >
            <h2 className="font-medium">
              {t('production.library')} · {product?.name}
            </h2>
            <SearchableSelect
              {...selectProps}
              value={categoryId}
              onChange={setCategoryId}
              options={[
                { value: '', label: w('allCategories') },
                { value: '__uncategorized', label: w('uncategorized') },
                ...(product?.categories ?? []).map((c) => ({ value: c.id, label: c.name })),
              ]}
              ariaLabel={w('categories')}
            />
            <div className="space-y-4 overflow-y-auto">
              {[
                ...(product?.categories ?? []),
                { id: '', name: w('uncategorized'), description: '' },
              ]
                .filter(
                  (c) =>
                    !categoryId || (categoryId === '__uncategorized' ? !c.id : c.id === categoryId),
                )
                .map((category) => {
                  const files = scopedAssets.filter(
                    (a) =>
                      (a.category_id && product?.categories.some((c) => c.id === a.category_id)
                        ? a.category_id
                        : '') === category.id,
                  );
                  return (
                    <section key={category.id} className="space-y-2">
                      <h3 className="text-sm font-medium">
                        {category.name} · {files.length}
                      </h3>
                      {category.description && (
                        <p className="text-xs text-muted-foreground">{category.description}</p>
                      )}
                      {files.map((a) => (
                        <article
                          key={a.id}
                          className="space-y-2 rounded-lg border border-border p-2 text-xs"
                        >
                          <div className="flex items-start gap-2">
                            <input
                              type="checkbox"
                              aria-label={a.filename}
                              checked={attachments.some((item) => item.id === a.id)}
                              disabled={
                                busy || a.video_type_id !== videoType?.id || roleOf(a) !== 'input'
                              }
                              onChange={(e) => {
                                setDiscussionOnly(false);
                                setSelected((ids) =>
                                  e.target.checked
                                    ? [...new Set([...ids, a.id])]
                                    : ids.filter((id) => id !== a.id),
                                );
                              }}
                            />
                            <button
                              type="button"
                              className="break-all text-left underline"
                              aria-label={t('common.play') + ' ' + a.filename}
                              onClick={() => setPreview(a)}
                            >
                              {a.filename}
                            </button>
                          </div>
                          {a.classification_status && (
                            <p className="break-words text-muted-foreground">
                              {w('classification', {
                                status: classificationKeys[a.classification_status]
                                  ? w(classificationKeys[a.classification_status])
                                  : a.classification_status,
                              })}
                            </p>
                          )}
                          <SearchableSelect
                            {...selectProps}
                            value={a.category_id ?? ''}
                            onChange={(id: string) =>
                              void updateAsset(a, { category_id: id || null })
                            }
                            options={[
                              { value: '', label: w('uncategorized') },
                              ...(product?.categories ?? []).map((c) => ({
                                value: c.id,
                                label: c.name,
                              })),
                            ]}
                            ariaLabel={w('category') + ' ' + a.filename}
                          />
                          <SearchableSelect
                            {...selectProps}
                            value={a.video_type_id ?? ''}
                            onChange={(id: string) => void updateAsset(a, { video_type_id: id })}
                            options={(product?.video_types ?? []).map((v) => ({
                              value: v.id,
                              label: v.name,
                            }))}
                            placeholder={w('chooseVideoType')}
                            ariaLabel={w('videoTypes') + ' ' + a.filename}
                          />
                          <SearchableSelect
                            {...selectProps}
                            value={roleOf(a)}
                            onChange={(role: Role) => void updateAsset(a, { role })}
                            options={roles.map((role) => ({
                              value: role,
                              label: w(roleKeys[role]),
                            }))}
                            ariaLabel={w('role') + ' ' + a.filename}
                          />
                        </article>
                      ))}
                    </section>
                  );
                })}
            </div>
          </aside>
        </div>
      </main>
      {editor && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open && !busy) setEditor(null);
          }}
        >
          <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>{w(editor.id ? 'editProduct' : 'newProduct')}</DialogTitle>
            </DialogHeader>
            <label className="grid gap-2 text-sm">
              {w('productName')}
              <Input
                value={editor.name}
                disabled={busy}
                onChange={(e) => setEditor({ ...editor, name: e.target.value })}
              />
            </label>
            <label className="grid gap-2 text-sm">
              {w('productNotes')}
              <Textarea
                value={editor.notes}
                disabled={busy}
                onChange={(e) => setEditor({ ...editor, notes: e.target.value })}
              />
            </label>
            <label className="grid gap-2 text-sm">
              {t('production.batch.pack')}
              <Input
                value={editor.pack_id}
                disabled={busy}
                onChange={(e) => setEditor({ ...editor, pack_id: e.target.value })}
              />
            </label>
            <div className="grid gap-2 text-sm">
              <span>{t('production.batch.voice')}</span>
              <VoiceSelector
                value={editor.voice_profile ?? ''}
                onChange={(id: string) => setEditor({ ...editor, voice_profile: id || null })}
                profiles={profiles.data ?? []}
                presets={false}
                gallery={false}
                ariaLabel={t('production.batch.voice')}
                menuPortal
              />
            </div>
            <label className="grid gap-2 text-sm">
              {t('avatar.engine')}
              <Input
                value={editor.tts_engine ?? ''}
                disabled={busy}
                onChange={(e) => setEditor({ ...editor, tts_engine: e.target.value || null })}
              />
            </label>
            <section className="space-y-3">
              <h3 className="font-medium">{w('categories')}</h3>
              {editor.categories.map((category, index) => (
                <div key={category.id} className="space-y-2 rounded-lg border border-border p-3">
                  <Input
                    aria-label={w('categoryName') + ' ' + (index + 1)}
                    placeholder={w('categoryName')}
                    value={category.name}
                    disabled={busy}
                    onChange={(e) =>
                      setEditor({
                        ...editor,
                        categories: editor.categories.map((c) =>
                          c.id === category.id ? { ...c, name: e.target.value } : c,
                        ),
                      })
                    }
                  />
                  <Textarea
                    aria-label={w('categoryDescription') + ' ' + (index + 1)}
                    placeholder={w('categoryDescription')}
                    value={category.description}
                    disabled={busy}
                    onChange={(e) =>
                      setEditor({
                        ...editor,
                        categories: editor.categories.map((c) =>
                          c.id === category.id ? { ...c, description: e.target.value } : c,
                        ),
                      })
                    }
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      setEditor({
                        ...editor,
                        categories: editor.categories.filter((c) => c.id !== category.id),
                      })
                    }
                  >
                    {w('removeCategory')}
                  </Button>
                </div>
              ))}
              <Button
                variant="outline"
                disabled={busy}
                onClick={() =>
                  setEditor({
                    ...editor,
                    categories: [...editor.categories, { id: newId(), name: '', description: '' }],
                  })
                }
              >
                {w('addCategory')}
              </Button>
            </section>
            <section className="space-y-3">
              <h3 className="font-medium">{w('videoTypes')}</h3>
              {editor.video_types.map((type, index) => (
                <div key={type.id} className="space-y-2 rounded-lg border border-border p-3">
                  <Input
                    aria-label={w('videoTypeName') + ' ' + (index + 1)}
                    placeholder={w('videoTypeName')}
                    value={type.name}
                    disabled={busy}
                    onChange={(e) =>
                      setEditor({
                        ...editor,
                        video_types: editor.video_types.map((v) =>
                          v.id === type.id ? { ...v, name: e.target.value } : v,
                        ),
                      })
                    }
                  />
                  <SearchableSelect
                    {...selectProps}
                    value={type.workflow}
                    onChange={(workflow: Workflow) =>
                      setEditor({
                        ...editor,
                        video_types: editor.video_types.map((v) =>
                          v.id === type.id ? { ...v, workflow } : v,
                        ),
                      })
                    }
                    options={workflows.map((value) => ({ value, label: w(workflowKeys[value]) }))}
                    ariaLabel={w('workflow') + ' ' + (index + 1)}
                  />
                  <Textarea
                    aria-label={w('typeInstruction') + ' ' + (index + 1)}
                    placeholder={w('typeInstruction')}
                    value={type.instruction}
                    disabled={busy}
                    onChange={(e) =>
                      setEditor({
                        ...editor,
                        video_types: editor.video_types.map((v) =>
                          v.id === type.id ? { ...v, instruction: e.target.value } : v,
                        ),
                      })
                    }
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      setEditor({
                        ...editor,
                        video_types: editor.video_types.filter((v) => v.id !== type.id),
                      })
                    }
                  >
                    {w('removeVideoType')}
                  </Button>
                </div>
              ))}
              <Button
                variant="outline"
                disabled={busy}
                onClick={() =>
                  setEditor({
                    ...editor,
                    video_types: [
                      ...editor.video_types,
                      { id: newId(), name: '', workflow: 'product', instruction: '' },
                    ],
                  })
                }
              >
                {w('addVideoType')}
              </Button>
            </section>
            {editor.id && (
              <div className="space-y-2">
                {deleting && (
                  <p role="alert" className="text-sm text-destructive">
                    {w('deleteConfirm', { name: editor.name })}
                  </p>
                )}
                <Button
                  variant="destructive"
                  disabled={busy}
                  onClick={() => {
                    if (!deleting) {
                      setDeleting(true);
                      return;
                    }
                    void action(async () => {
                      await apiJson(base + '/products/' + editor.id, { method: 'DELETE' });
                      setEditor(null);
                      chooseProduct('');
                      await refresh();
                    });
                  }}
                >
                  {w('deleteProduct')}
                </Button>
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" disabled={busy} onClick={() => setEditor(null)}>
                {t('common.cancel')}
              </Button>
              <Button
                disabled={
                  busy ||
                  !editor.name.trim() ||
                  !editor.pack_id.trim() ||
                  editor.categories.some((c) => !c.name.trim()) ||
                  editor.video_types.some((v) => !v.name.trim())
                }
                onClick={() => void saveProduct()}
              >
                {t('common.save')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
      <Dialog
        open={!!preview}
        onOpenChange={(open) => {
          if (!open) setPreview(null);
        }}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{preview?.filename}</DialogTitle>
          </DialogHeader>
          {preview &&
            (preview.kind === 'audio' ? (
              <audio
                src={apiPath(base + '/assets/' + preview.id + '/preview')}
                controls
                className="w-full"
              />
            ) : preview.kind === 'image' ? (
              <img
                src={apiPath(base + '/assets/' + preview.id + '/preview')}
                alt={preview.filename}
                className="max-h-96 max-w-full object-contain"
              />
            ) : preview.kind === 'text' ? (
              <a
                href={apiPath(base + '/assets/' + preview.id + '/preview')}
                target="_blank"
                rel="noreferrer"
              >
                {preview.filename}
              </a>
            ) : (
              <video
                src={apiPath(base + '/assets/' + preview.id + '/preview')}
                controls
                playsInline
                className="max-h-96 w-full"
              />
            ))}
        </DialogContent>
      </Dialog>
    </div>
  );
}
