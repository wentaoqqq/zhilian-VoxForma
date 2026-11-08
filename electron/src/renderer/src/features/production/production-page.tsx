import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ClapperboardIcon,
  UploadIcon,
  PlayIcon,
  PlusIcon,
  SettingsIcon,
  DownloadIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
// @ts-expect-error Existing shared JSX controls have no declaration files.
import SearchableSelect from '@shared/components/SearchableSelect';
// @ts-expect-error Reuse Studio's native voice selector rather than inventing another voice model.
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

type Workflow = 'product' | 'talking_head' | 'narration' | 'intake';
interface Asset {
  id: string;
  filename: string;
  kind?: 'video' | 'audio' | 'image' | 'text';
  duration: number;
  bytes: number;
  width: number;
  height: number;
}
interface Brand {
  id: string;
  name: string;
  pack_id: string;
  notes: string;
  outro_ids: string[];
  material_ids: string[];
  sticker_ids: string[];
  voice_profile: string | null;
  tts_engine: string | null;
}
interface Item {
  source_id: string;
  filename: string;
  status: string;
  error?: string;
  artifacts: Record<string, string>;
}
interface Batch {
  id: string;
  status: string;
  error?: string;
  items: Item[];
  log: { time: number; kind: string; text: string }[];
  request: { options: { workflow: Workflow }; brand: Brand };
}
interface AgentStatus {
  planner: { ready: boolean };
  vision: { ready: boolean };
  runtime: { installed: boolean; packs: string[]; job?: { status: string; error?: string } | null };
}
const base = '/production';
const picker = 'w-full rounded-md border border-border bg-background px-3 py-2 text-left text-sm';
const blankBrand: Omit<Brand, 'id'> = {
  name: '',
  pack_id: 'roundtable',
  notes: '',
  outro_ids: [],
  material_ids: [],
  sticker_ids: [],
  voice_profile: null,
  tts_engine: null,
};
function remembered(key: string) {
  try {
    return localStorage.getItem(key) || '';
  } catch {
    return '';
  }
}

export function ProductionPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const profiles = useProfiles();
  const [tab, setTab] = useState<'work' | 'library' | 'brands' | 'results'>('work');
  const [brandId, setBrandId] = useState(() => remembered('voxforma.production.brand'));
  const [workflow, setWorkflow] = useState<Workflow>('product');
  const [selected, setSelected] = useState<string[]>([]);
  const [explicitSelection, setExplicitSelection] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState('');
  const [editor, setEditor] = useState<Brand | Omit<Brand, 'id'> | null>(null);
  const [preview, setPreview] = useState<Asset | null>(null);
  const [openLogs, setOpenLogs] = useState<string[]>([]);
  const uploadRef = useRef<HTMLInputElement>(null);
  const packRef = useRef<HTMLInputElement>(null);
  const submission = useRef<{ signature: string; id: string } | null>(null);
  const assets = useQuery({
    queryKey: ['production-assets'],
    queryFn: () => apiJson<Asset[]>(base + '/assets'),
  });
  const brands = useQuery({
    queryKey: ['production-brands'],
    queryFn: () => apiJson<Brand[]>(base + '/brands'),
  });
  const status = useQuery({
    queryKey: ['production-agent-status'],
    queryFn: () => apiJson<AgentStatus>(base + '/agent/status'),
    refetchInterval: 10000,
  });
  const batches = useQuery({
    queryKey: ['production-batches'],
    queryFn: () => apiJson<Batch[]>(base + '/batches'),
    refetchInterval: 3000,
  });
  const currentBrand = brands.data?.find((b) => b.id === brandId);
  useEffect(() => {
    if (!brandId && brands.data?.length === 1) {
      setBrandId(brands.data[0].id);
      try {
        localStorage.setItem('voxforma.production.brand', brands.data[0].id);
      } catch {
        /* Selection remains in memory. */
      }
    }
  }, [brandId, brands.data]);
  const validKinds =
    workflow === 'narration'
      ? ['audio', 'text']
      : workflow === 'intake'
        ? ['video', 'image']
        : ['video'];
  const finishedOrActive = new Set(
    (batches.data ?? [])
      .filter((b) => b.request.brand.id === brandId && b.request.options.workflow === workflow)
      .flatMap((b) =>
        b.items
          .filter((i) => i.status === 'done' || ['pending', 'running'].includes(b.status))
          .map((i) => i.source_id),
      ),
  );
  const eligible = (assets.data ?? []).filter(
    (a) =>
      validKinds.includes(a.kind ?? 'video') &&
      !currentBrand?.outro_ids.includes(a.id) &&
      !currentBrand?.sticker_ids.includes(a.id),
  );
  const inputs = eligible.filter(
    (a) =>
      selected.includes(a.id) ||
      (!finishedOrActive.has(a.id) && !currentBrand?.material_ids.includes(a.id)),
  );
  const chosen = explicitSelection
    ? selected.filter((id) => eligible.some((a) => a.id === id))
    : inputs.map((a) => a.id);
  const listed = tab === 'library' ? (assets.data ?? []) : inputs;
  const ready = !!(
    status.data?.planner.ready &&
    status.data?.vision.ready &&
    status.data?.runtime.installed &&
    currentBrand &&
    status.data.runtime.packs.includes(currentBrand.pack_id)
  );
  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['production-assets'] }),
      client.invalidateQueries({ queryKey: ['production-brands'] }),
      client.invalidateQueries({ queryKey: ['production-batches'] }),
      client.invalidateQueries({ queryKey: ['production-agent-status'] }),
    ]);
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
  function chooseBrand(id: string) {
    setBrandId(id);
    setExplicitSelection(false);
    try {
      localStorage.setItem('voxforma.production.brand', id);
    } catch {
      /* Selection remains in memory. */
    }
  }
  async function upload(files: File[]) {
    await action(async () => {
      const ids: string[] = [];
      for (const file of files) {
        setUploading(file.name);
        const result = await apiJson<Asset>(
          base + '/assets?' + new URLSearchParams({ filename: file.name }),
          { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } },
        );
        ids.push(result.id);
      }
      setSelected((current) => Array.from(new Set([...current, ...ids])));
      setExplicitSelection(true);
      await refresh();
    });
  }
  async function start() {
    const snapshot = {
      brand_id: brandId,
      workflow,
      source_ids: chosen,
      instruction: instruction.trim() || t('production.batch.start'),
    };
    const signature = JSON.stringify(snapshot);
    if (submission.current?.signature !== signature)
      submission.current = {
        signature,
        id: Date.now().toString(36) + '-' + Math.random().toString(36).slice(2),
      };
    const requestId = submission.current.id;
    await action(async () => {
      const result = await apiJson<Batch>(base + '/batches', {
        method: 'POST',
        body: JSON.stringify({ ...snapshot, request_id: requestId }),
      });
      submission.current = null;
      setSelected((current) => current.filter((id) => !snapshot.source_ids.includes(id)));
      setExplicitSelection(false);
      setOpenLogs((current) => [result.id, ...current]);
      await refresh();
    });
  }
  async function saveBrand() {
    if (!editor) return;
    await action(async () => {
      const id = 'id' in editor ? editor.id : null;
      const saved = await apiJson<Brand>(base + '/brands' + (id ? '/' + id : ''), {
        method: id ? 'PUT' : 'POST',
        body: JSON.stringify(editor),
      });
      chooseBrand(saved.id);
      setEditor(null);
      setTab('work');
      await refresh();
    });
  }
  async function jobAction(id: string, verb: 'cancel' | 'retry') {
    await action(async () => {
      await apiJson(base + '/batches/' + id + '/' + verb, { method: 'POST' });
      await refresh();
    });
  }
  const toggle = (id: string) => {
    setSelected(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);
    setExplicitSelection(true);
  };
  const state = (value: string) => t('batch.status_' + (value === 'pending' ? 'queued' : value));
  const media = (a: Asset) =>
    a.kind === 'audio' ? (
      <audio src={apiPath(base + '/assets/' + a.id + '/preview')} controls className="w-full" />
    ) : a.kind === 'image' ? (
      <img
        src={apiPath(base + '/assets/' + a.id + '/preview')}
        alt={a.filename}
        className="mx-auto max-h-96 max-w-full object-contain"
      />
    ) : a.kind === 'text' ? (
      <a href={apiPath(base + '/assets/' + a.id + '/preview')} target="_blank" rel="noreferrer">
        {a.filename}
      </a>
    ) : (
      <video
        src={apiPath(base + '/assets/' + a.id + '/preview')}
        controls
        playsInline
        preload="metadata"
        className="mx-auto max-h-[65vh] max-w-full rounded bg-black object-contain"
      />
    );
  const uploadInput = (
    <input
      ref={uploadRef}
      type="file"
      multiple
      accept="video/*,audio/*,image/*,.txt,.mkv"
      className="sr-only"
      aria-hidden="true"
      tabIndex={-1}
      disabled={busy}
      data-testid="batch-upload"
      onChange={(e) => {
        const files = Array.from(e.currentTarget.files ?? []);
        e.currentTarget.value = '';
        if (files.length) void upload(files);
      }}
    />
  );
  function pool(
    key: 'outro_ids' | 'material_ids' | 'sticker_ids',
    kind: 'video' | 'image',
    label: string,
  ) {
    return (
      <div className="grid gap-2">
        <h3 className="text-sm font-medium">{label}</h3>
        <div className="grid max-h-48 gap-2 overflow-y-auto rounded-lg border border-border p-3">
          {(assets.data ?? [])
            .filter((a) => (a.kind ?? 'video') === kind)
            .map((a) => (
              <label key={a.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  disabled={busy}
                  checked={editor?.[key].includes(a.id) ?? false}
                  onChange={() =>
                    setEditor((current) =>
                      current
                        ? {
                            ...current,
                            [key]: current[key].includes(a.id)
                              ? current[key].filter((id) => id !== a.id)
                              : [...current[key], a.id],
                          }
                        : current,
                    )
                  }
                />
                <span className="min-w-0 flex-1 truncate">{a.filename}</span>
                <button type="button" className="text-xs underline" onClick={() => setPreview(a)}>
                  {t('common.play')}
                </button>
              </label>
            ))}
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="flex items-center gap-2">
          <ClapperboardIcon className="size-4" />
          {t('production.title')}
        </h1>
      </WorkspaceHeader>
      <main className="flex-1 overflow-y-auto p-5" data-testid="production-page">
        <div className="mx-auto grid max-w-6xl gap-5">
          <div className="flex flex-wrap gap-2">
            {(['work', 'library', 'brands', 'results'] as const).map((key) => (
              <Button
                key={key}
                variant={tab === key ? 'default' : 'outline'}
                onClick={() => setTab(key)}
              >
                {t(
                  key === 'work'
                    ? 'production.batch.workbench'
                    : key === 'library'
                      ? 'production.library'
                      : key === 'brands'
                        ? 'production.batch.brands'
                        : 'production.deliveries',
                )}
              </Button>
            ))}
          </div>
          {(error || assets.isError || brands.isError || batches.isError || status.isError) && (
            <p role="alert" className="break-words text-sm text-destructive">
              {error ||
                describeError(assets.error || brands.error || batches.error || status.error)}
            </p>
          )}
          {busy && (
            <p role="status" className="text-sm">
              {t('common.saving')} {uploading}
            </p>
          )}
          {!editor && uploadInput}
          <input
            ref={packRef}
            type="file"
            accept=".zip"
            className="sr-only"
            aria-hidden="true"
            tabIndex={-1}
            disabled={busy}
            onChange={(e) => {
              const file = e.currentTarget.files?.[0];
              e.currentTarget.value = '';
              if (file)
                void action(async () => {
                  await apiJson(base + '/packs/' + (currentBrand?.pack_id || 'roundtable'), {
                    method: 'POST',
                    body: file,
                    headers: { 'Content-Type': 'application/zip' },
                  });
                  await refresh();
                });
            }}
          />
          {tab === 'work' && (
            <>
              <section className="grid gap-4 rounded-xl border border-border p-5 sm:grid-cols-2">
                <div className="grid content-start gap-2">
                  <span className="text-sm">{t('production.batch.workflow')}</span>
                  <SearchableSelect
                    value={workflow}
                    onChange={(v: Workflow) => {
                      setWorkflow(v);
                      setSelected([]);
                      setExplicitSelection(false);
                    }}
                    options={(['product', 'talking_head', 'narration', 'intake'] as const).map(
                      (value) => ({ value, label: t('production.batch.' + value) }),
                    )}
                    ariaLabel={t('production.batch.workflow')}
                    menuPortal
                    buttonClassName={picker}
                    disabled={busy}
                  />
                </div>
                <div className="grid content-start gap-2">
                  <span className="text-sm">{t('production.batch.brands')}</span>
                  <div className="flex gap-2">
                    <SearchableSelect
                      value={brandId}
                      onChange={chooseBrand}
                      options={(brands.data ?? []).map((b) => ({ value: b.id, label: b.name }))}
                      placeholder={t('production.batch.chooseBrand')}
                      ariaLabel={t('production.batch.brands')}
                      menuPortal
                      buttonClassName={picker}
                      disabled={busy}
                    />
                    <Button
                      variant="outline"
                      size="icon"
                      aria-label={t('production.batch.brands')}
                      onClick={() => setTab('brands')}
                    >
                      <SettingsIcon />
                    </Button>
                  </div>
                </div>
                <div className="grid gap-3 sm:col-span-2">
                  <Textarea
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    disabled={busy}
                    rows={3}
                    placeholder={t('production.batch.instruction')}
                    aria-label={t('production.batch.instruction')}
                  />
                  <p className="text-xs text-muted-foreground">{t('production.batch.cloudHint')}</p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      onClick={() => void start()}
                      disabled={busy || !ready || !chosen.length}
                      size="lg"
                    >
                      <PlayIcon />
                      {t('production.batch.start')} · {chosen.length}
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() => uploadRef.current?.click()}
                    >
                      <UploadIcon />
                      {t('production.batch.upload')}
                    </Button>
                  </div>
                </div>
              </section>
              {!ready && (
                <section className="grid gap-3 rounded-xl border border-border p-4">
                  <p className="text-sm">{t('production.batch.setupHint')}</p>
                  {status.data?.runtime.job?.status === 'failed' && (
                    <p role="alert" className="text-sm text-destructive">
                      {status.data.runtime.job.error}
                    </p>
                  )}
                  {['pending', 'running'].includes(status.data?.runtime.job?.status ?? '') && (
                    <p role="status" className="text-sm">
                      {t('common.saving')}
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <a href="#/settings/models/llm" className="text-sm underline">
                      {t('production.ai.settings')}
                    </a>
                    {!status.data?.runtime.installed && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={
                          busy ||
                          ['pending', 'running'].includes(status.data?.runtime.job?.status ?? '')
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
                    {!status.data?.runtime.packs.includes(
                      currentBrand?.pack_id || 'roundtable',
                    ) && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => packRef.current?.click()}
                      >
                        {t('production.batch.importPack')}
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setEditor({ ...blankBrand });
                        setTab('brands');
                      }}
                    >
                      {t('production.batch.newBrand')}
                    </Button>
                  </div>
                </section>
              )}
            </>
          )}
          {(tab === 'work' || tab === 'library') && (
            <section className="grid gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="font-medium">
                  {t(tab === 'work' ? 'production.batch.pending' : 'production.library')} ·{' '}
                  {listed.length}
                </h2>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    setSelected(listed.map((a) => a.id));
                    setExplicitSelection(true);
                  }}
                >
                  {t('production.batch.selectAll')}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    setSelected([]);
                    setExplicitSelection(true);
                  }}
                >
                  {t('common.clear')}
                </Button>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {listed.map((a) => (
                  <div
                    key={a.id}
                    className="flex items-center gap-3 rounded-lg border border-border p-3"
                  >
                    <input
                      type="checkbox"
                      checked={chosen.includes(a.id)}
                      disabled={busy}
                      aria-label={a.filename}
                      onChange={() => toggle(a.id)}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm" title={a.filename}>
                        {a.filename}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {a.kind ?? 'video'} · {a.duration ? a.duration.toFixed(1) + 's · ' : ''}
                        {(a.bytes / 1024 ** 2).toFixed(1)} MiB
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t('common.play') + ' ' + a.filename}
                      onClick={() => setPreview(a)}
                    >
                      <PlayIcon />
                    </Button>
                  </div>
                ))}
              </div>
              {tab === 'library' && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => uploadRef.current?.click()}
                >
                  <UploadIcon />
                  {t('production.batch.upload')}
                </Button>
              )}
            </section>
          )}
          {tab === 'brands' && (
            <section className="grid gap-4">
              <div className="flex justify-end">
                <Button disabled={busy} onClick={() => setEditor({ ...blankBrand })}>
                  <PlusIcon />
                  {t('production.batch.newBrand')}
                </Button>
              </div>
              {(brands.data ?? []).map((brand) => (
                <article
                  key={brand.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-5"
                >
                  <div>
                    <h2 className="font-medium">{brand.name}</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {t('production.batch.outros')}: {brand.outro_ids.length} ·{' '}
                      {t('production.batch.materials')}: {brand.material_ids.length}
                    </p>
                  </div>
                  <Button variant="outline" disabled={busy} onClick={() => setEditor({ ...brand })}>
                    {t('common.details')}
                  </Button>
                </article>
              ))}
            </section>
          )}
          {(tab === 'work' || tab === 'results') && (
            <section className="grid gap-4">
              <h2 className="font-medium">
                {t(tab === 'results' ? 'production.deliveries' : 'avatar.queue')}
              </h2>
              {!batches.data?.length && (
                <p className="text-sm text-muted-foreground">{t('production.noRuns')}</p>
              )}
              {(batches.data ?? []).map((batch) => {
                const done = batch.items.filter((i) => i.status === 'done').length;
                const active = ['pending', 'running'].includes(batch.status);
                return (
                  <article
                    key={batch.id}
                    className="grid gap-3 rounded-xl border border-border p-4"
                  >
                    <div className="flex flex-wrap justify-between gap-2">
                      <h3 className="font-medium">
                        {batch.request.brand.name} ·{' '}
                        {t('production.batch.' + batch.request.options.workflow)}
                      </h3>
                      <span className="text-sm">
                        {done}/{batch.items.length} · {state(batch.status)}
                      </span>
                    </div>
                    {batch.error && <p className="text-sm text-destructive">{batch.error}</p>}
                    <div className="grid gap-2">
                      {batch.items.map((item) => (
                        <div key={item.source_id} className="rounded-lg border border-border p-3">
                          <div className="flex justify-between gap-3 text-sm">
                            <span className="truncate">{item.filename}</span>
                            <span>
                              {state(
                                !active && item.status === 'pending' ? batch.status : item.status,
                              )}
                            </span>
                          </div>
                          {item.error && (
                            <p className="mt-2 break-words text-xs text-destructive">
                              {item.error}
                            </p>
                          )}
                          {item.status === 'done' && item.artifacts.video && (
                            <div className="mt-3 grid gap-3 sm:grid-cols-2">
                              <video
                                src={apiPath(
                                  base +
                                    '/batches/' +
                                    batch.id +
                                    '/items/' +
                                    item.source_id +
                                    '/files/video',
                                )}
                                controls
                                preload="metadata"
                                playsInline
                                className="mx-auto aspect-[9/16] h-80 w-auto max-w-full rounded bg-black object-contain"
                              />
                              <img
                                src={apiPath(
                                  base +
                                    '/batches/' +
                                    batch.id +
                                    '/items/' +
                                    item.source_id +
                                    '/files/cover',
                                )}
                                alt={item.filename}
                                loading="lazy"
                                className="mx-auto aspect-[3/4] h-60 w-auto max-w-full rounded object-contain"
                              />
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          setOpenLogs((current) =>
                            current.includes(batch.id)
                              ? current.filter((id) => id !== batch.id)
                              : [...current, batch.id],
                          )
                        }
                      >
                        {t('production.batch.log')}
                      </Button>
                      {active ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => void jobAction(batch.id, 'cancel')}
                        >
                          {t('common.cancel')}
                        </Button>
                      ) : done < batch.items.length ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => void jobAction(batch.id, 'retry')}
                        >
                          {t('production.batch.retryFailed')}
                        </Button>
                      ) : null}
                      {done > 0 && batch.request.options.workflow !== 'intake' && (
                        <a
                          className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-sm"
                          href={apiPath(base + '/batches/' + batch.id + '/download')}
                        >
                          <DownloadIcon className="size-4" />
                          {t('production.download')}
                        </a>
                      )}
                    </div>
                    {openLogs.includes(batch.id) && (
                      <div className="max-h-96 overflow-auto rounded-lg bg-muted/40 p-3" role="log">
                        {batch.log.map((event, index) => (
                          <p
                            key={index}
                            className="mb-2 whitespace-pre-wrap break-words font-mono text-xs"
                          >
                            [{event.kind}] {event.text}
                          </p>
                        ))}
                      </div>
                    )}
                  </article>
                );
              })}
            </section>
          )}
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
              <DialogTitle>{t('production.batch.brands')}</DialogTitle>
            </DialogHeader>
            <label className="grid gap-2 text-sm">
              {t('production.batch.brandName')}
              <Input
                value={editor.name}
                disabled={busy}
                onChange={(e) => setEditor({ ...editor, name: e.target.value })}
              />
            </label>
            <label className="grid gap-2 text-sm">
              {t('production.batch.brandNotes')}
              <Textarea
                rows={4}
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
            {uploadInput}
            {pool('outro_ids', 'video', t('production.batch.outros'))}
            {pool('material_ids', 'video', t('production.batch.materials'))}
            {pool('sticker_ids', 'image', t('production.batch.stickers'))}
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
            <Button variant="outline" disabled={busy} onClick={() => uploadRef.current?.click()}>
              <UploadIcon />
              {t('production.batch.upload')}
            </Button>
            <DialogFooter>
              <Button variant="outline" disabled={busy} onClick={() => setEditor(null)}>
                {t('common.cancel')}
              </Button>
              <Button disabled={busy || !editor.name.trim()} onClick={() => void saveBrand()}>
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
          {preview && media(preview)}
        </DialogContent>
      </Dialog>
    </div>
  );
}
