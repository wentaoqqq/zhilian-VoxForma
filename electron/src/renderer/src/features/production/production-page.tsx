import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ClapperboardIcon, DownloadIcon, UploadIcon, SparklesIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
// @ts-expect-error Shared legacy JSX control has no declaration file.
import SearchableSelect from '@shared/components/SearchableSelect';
import { apiFetch, apiJson, apiPath, describeError } from '@/lib/api/client';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

interface Asset {
  id: string;
  filename: string;
  duration: number;
  width: number;
  height: number;
  bytes: number;
}
interface Caption {
  start: number;
  end: number;
  text: string;
}
interface Plan {
  schema_version: 1;
  plugin_id: string;
  source_id: string;
  outro_id: string;
  title: string;
  cover_title: string;
  publication_title: string;
  brand_text: string;
  subtitle_mode: 'keep' | 'none' | 'provided';
  captions: Caption[];
  cover_time: number;
}
interface Run {
  id: string;
  plan: Plan;
  status: string;
  error: string | null;
  steps: Record<string, string>;
  artifacts: Record<string, string>;
}
const prefix = '/production';
const emptyPlan: Plan = {
  schema_version: 1,
  plugin_id: 'general',
  source_id: '',
  outro_id: '',
  title: '',
  cover_title: '',
  publication_title: '',
  brand_text: '',
  subtitle_mode: 'keep',
  captions: [],
  cover_time: 0,
};
const pickerClass =
  'w-full rounded-md border border-border bg-background px-3 py-2 text-left text-sm';
const draftKey = 'voxforma.production.draft.v1';
function savedDraft(): Plan {
  try {
    const saved = JSON.parse(localStorage.getItem(draftKey) || 'null');
    if (
      saved &&
      Object.keys(emptyPlan).every((key) =>
        key === 'captions'
          ? Array.isArray(saved[key]) &&
            saved[key].every(
              (c: Caption) =>
                typeof c.text === 'string' && Number.isFinite(c.start) && Number.isFinite(c.end),
            )
          : typeof saved[key] === typeof emptyPlan[key as keyof Plan],
      )
    )
      return { ...emptyPlan, ...saved };
  } catch {
    /* The workspace also works when browser storage is unavailable. */
  }
  return { ...emptyPlan };
}
interface AIResult {
  plan: Plan;
  explanation: string;
  issues: string[];
  ready: boolean;
  run: Run | null;
}

export function ProductionPage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [tab, setTab] = useState<'work' | 'library' | 'deliveries'>('work');
  const [plan, setPlan] = useState<Plan>(savedDraft);
  const [instruction, setInstruction] = useState('');
  const [aiResult, setAiResult] = useState<AIResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [srtName, setSrtName] = useState('');
  const uploadRef = useRef<HTMLInputElement>(null);
  const srtRef = useRef<HTMLInputElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const uploadTarget = useRef<'source_id' | 'outro_id'>('source_id');
  useEffect(() => {
    try {
      localStorage.setItem(draftKey, JSON.stringify(plan));
    } catch {
      /* No credentials are stored in the draft. */
    }
  }, [plan]);
  const plugins = useQuery({
    queryKey: ['production-plugins'],
    queryFn: () => apiJson<{ id: string; name_key: string }[]>(prefix + '/plugins'),
  });
  const aiStatus = useQuery({
    queryKey: ['production-ai-status'],
    queryFn: () =>
      apiJson<{ ready: boolean; provider_display_name: string }>(prefix + '/ai/status'),
    refetchInterval: 15000,
  });
  const assets = useQuery({
    queryKey: ['production-assets'],
    queryFn: () => apiJson<Asset[]>(prefix + '/assets'),
  });
  const runs = useQuery({
    queryKey: ['production-runs'],
    queryFn: () => apiJson<Run[]>(prefix + '/runs'),
    refetchInterval: 3000,
  });
  const source = assets.data?.find((a) => a.id === plan.source_id);
  const outro = assets.data?.find((a) => a.id === plan.outro_id);
  const options = (assets.data ?? []).map((a) => ({
    value: a.id,
    label: a.filename + ' · ' + a.duration.toFixed(1) + 's',
  }));
  const update = <K extends keyof Plan>(key: K, value: Plan[K]) =>
    setPlan((current) => ({ ...current, [key]: value }));
  function chooseSource(asset: Asset) {
    setPlan((current) =>
      current.source_id === asset.id
        ? current
        : {
            ...current,
            source_id: asset.id,
            cover_time: 0,
            captions: [],
            subtitle_mode: 'keep',
            ...Object.fromEntries(
              ['title', 'cover_title', 'publication_title'].map((key) => [
                key,
                asset.filename.replace(/\.[^.]+$/, ''),
              ]),
            ),
          },
    );
    setSrtName('');
    setAiResult(null);
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
    }
  }
  async function upload(file: File) {
    const target = uploadTarget.current;
    await action(async () => {
      const data = await apiJson<Asset>(
        prefix + '/assets?' + new URLSearchParams({ filename: file.name }),
        { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } },
      );
      await client.invalidateQueries({ queryKey: ['production-assets'] });
      if (target === 'source_id') chooseSource({ ...data, filename: file.name });
      else {
        update('outro_id', data.id);
        setAiResult(null);
      }
    });
  }
  async function submit() {
    await action(async () => {
      await apiJson(prefix + '/runs', { method: 'POST', body: JSON.stringify(plan) });
      await client.invalidateQueries({ queryKey: ['production-runs'] });
    });
  }
  async function askAI(submit: boolean) {
    const snapshot = plan;
    setAiResult(null);
    await action(async () => {
      const result = await apiJson<AIResult>(prefix + '/ai/plan', {
        method: 'POST',
        body: JSON.stringify({ draft: snapshot, instruction, submit }),
      });
      setPlan(result.plan);
      setAiResult(result);
      if (result.run) await client.invalidateQueries({ queryKey: ['production-runs'] });
    });
  }
  async function jobAction(id: string, verb: 'retry' | 'cancel') {
    await action(async () => {
      await apiJson(prefix + '/runs/' + id + '/' + verb, { method: 'POST' });
      await client.invalidateQueries({ queryKey: ['production-runs'] });
    });
  }
  async function download(run: Run) {
    await action(async () => {
      const response = await apiFetch(prefix + '/runs/' + run.id + '/download');
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement('a');
      a.href = url;
      a.download = run.plan.title.replace(/[<>:"/\\|?*]/g, '_') + '.zip';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    });
  }
  const stateLabel = (status: string) =>
    t('batch.status_' + (status === 'pending' ? 'queued' : status));
  const preview = (asset: Asset) => (
    <video
      key={asset.id}
      src={apiPath(prefix + '/assets/' + asset.id + '/preview')}
      controls
      playsInline
      preload="metadata"
      className="mx-auto aspect-[9/16] max-h-72 w-full rounded-lg bg-black object-contain"
    />
  );
  const chooseUpload = (target: 'source_id' | 'outro_id') => {
    uploadTarget.current = target;
    uploadRef.current?.click();
  };
  const tasks = (runs.data ?? []).filter((r) => tab !== 'deliveries' || r.status === 'done');
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="flex items-center gap-2">
          <ClapperboardIcon className="size-4" />
          {t('production.title')}
        </h1>
      </WorkspaceHeader>
      <main ref={mainRef} className="flex-1 overflow-y-auto p-5" data-testid="production-page">
        <div className="mx-auto grid max-w-6xl gap-5">
          <div className="flex flex-wrap gap-2" role="group" aria-label={t('production.title')}>
            {(['work', 'library', 'deliveries'] as const).map((key) => (
              <Button
                key={key}
                variant={tab === key ? 'default' : 'outline'}
                onClick={() => setTab(key)}
              >
                {t(
                  key === 'work'
                    ? 'production.product'
                    : key === 'library'
                      ? 'production.library'
                      : 'production.deliveries',
                )}
              </Button>
            ))}
          </div>
          <p className="text-sm text-muted-foreground">{t('production.hint')}</p>
          {(error || assets.isError || runs.isError) && (
            <p role="alert" className="text-sm text-destructive">
              {error || describeError(assets.error || runs.error)}
            </p>
          )}
          {busy && (
            <p role="status" className="text-sm">
              {t('common.saving')}
            </p>
          )}
          <input
            ref={uploadRef}
            aria-hidden="true"
            type="file"
            accept="video/*,.mkv"
            className="sr-only"
            tabIndex={-1}
            aria-label={t('gallery.upload')}
            disabled={busy}
            onChange={(e) => {
              const file = e.currentTarget.files?.[0];
              e.currentTarget.value = '';
              if (file) void upload(file);
            }}
          />
          <input
            ref={srtRef}
            aria-hidden="true"
            type="file"
            accept=".srt,.vtt"
            className="sr-only"
            tabIndex={-1}
            aria-label={t('production.srt')}
            disabled={busy}
            onChange={(e) => {
              const file = e.currentTarget.files?.[0];
              e.currentTarget.value = '';
              if (file)
                void action(async () => {
                  const captions = await apiJson<Caption[]>(prefix + '/subtitles/parse', {
                    method: 'POST',
                    body: JSON.stringify({ text: await file.text() }),
                  });
                  setPlan((current) => ({ ...current, captions, subtitle_mode: 'provided' }));
                  setSrtName(file.name);
                });
            }}
          />
          {tab === 'work' && (
            <div className="grid items-start gap-5 lg:grid-cols-2">
              <section className="grid gap-4 rounded-xl border border-border p-5">
                {(['source_id', 'outro_id'] as const).map((key) => (
                  <div key={key} className="grid gap-2">
                    <div className="flex items-center justify-between">
                      <h2 className="text-sm font-medium">
                        {t(key === 'source_id' ? 'production.source' : 'production.outro')}
                      </h2>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => chooseUpload(key)}
                      >
                        <UploadIcon />
                        {t('gallery.upload')}
                      </Button>
                    </div>
                    <SearchableSelect
                      value={plan[key]}
                      onChange={(value: string) => {
                        const asset = assets.data?.find((a) => a.id === value);
                        if (key === 'source_id' && asset) chooseSource(asset);
                        else {
                          update(key, value);
                          setAiResult(null);
                        }
                      }}
                      options={options}
                      placeholder={t('production.choose')}
                      ariaLabel={t(key === 'source_id' ? 'production.source' : 'production.outro')}
                      disabled={busy}
                      menuPortal
                      buttonClassName={pickerClass}
                    />
                    {(key === 'source_id' ? source : outro) &&
                      preview((key === 'source_id' ? source : outro)!)}
                  </div>
                ))}
              </section>
              <section className="grid gap-4 rounded-xl border border-border p-5">
                <div className="grid gap-3 rounded-lg border border-border bg-muted/25 p-4">
                  <h2 className="flex items-center gap-2 text-sm font-medium">
                    <SparklesIcon className="size-4" />
                    {t('production.ai.title')}
                  </h2>
                  <p className="text-xs text-muted-foreground">{t('production.ai.privacy')}</p>
                  <label className="grid gap-2 text-sm">
                    {t('production.plugin')}
                    <SearchableSelect
                      value={plan.plugin_id}
                      onChange={(value: string) => {
                        update('plugin_id', value);
                        setAiResult(null);
                      }}
                      options={(plugins.data ?? []).map((p) => ({
                        value: p.id,
                        label: t(p.name_key),
                      }))}
                      ariaLabel={t('production.plugin')}
                      disabled={busy}
                      menuPortal
                      buttonClassName={pickerClass}
                    />
                  </label>
                  <Textarea
                    aria-label={t('production.ai.instruction')}
                    placeholder={t('production.ai.instruction')}
                    value={instruction}
                    maxLength={10000}
                    rows={3}
                    disabled={busy}
                    onChange={(e) => setInstruction(e.target.value)}
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      disabled={
                        busy || !source || !outro || !instruction.trim() || !aiStatus.data?.ready
                      }
                      onClick={() => void askAI(false)}
                    >
                      {t('production.ai.plan')}
                    </Button>
                    <Button
                      disabled={
                        busy || !source || !outro || !instruction.trim() || !aiStatus.data?.ready
                      }
                      onClick={() => void askAI(true)}
                    >
                      {t('production.ai.generate')}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {aiStatus.data?.ready
                      ? aiStatus.data.provider_display_name
                      : t('production.ai.configure')}{' '}
                    ·{' '}
                    <a className="underline" href="#/settings/models/llm">
                      {t('production.ai.settings')}
                    </a>
                  </p>
                  {aiResult && (
                    <div className="grid gap-2 text-sm" role="status">
                      <p>{aiResult.explanation}</p>
                      {aiResult.issues.map((issue, i) => (
                        <p key={i} className="text-destructive">
                          {issue}
                        </p>
                      ))}
                      {aiResult.run && <p>{t('production.ai.queued')}</p>}
                    </div>
                  )}
                </div>
                {(['title', 'cover_title', 'publication_title', 'brand_text'] as const).map(
                  (key, index) => (
                    <label key={key} className="grid gap-2 text-sm">
                      {t(
                        [
                          'production.opening',
                          'production.cover',
                          'production.publication',
                          'production.brand',
                        ][index],
                      )}
                      <Input
                        value={plan[key]}
                        maxLength={
                          key === 'publication_title' ? 500 : key === 'brand_text' ? 80 : 120
                        }
                        disabled={busy}
                        onChange={(e) => update(key, e.target.value)}
                      />
                    </label>
                  ),
                )}
                <div className="grid gap-2 text-sm">
                  <span>{t('production.subtitles')}</span>
                  <SearchableSelect
                    value={plan.subtitle_mode}
                    onChange={(value: Plan['subtitle_mode']) => update('subtitle_mode', value)}
                    options={[
                      { value: 'keep', label: t('production.keep') },
                      { value: 'none', label: t('production.none') },
                      ...(plan.captions.length
                        ? [{ value: 'provided', label: t('production.srt') }]
                        : []),
                    ]}
                    ariaLabel={t('production.subtitles')}
                    disabled={busy}
                    menuPortal
                    buttonClassName={pickerClass}
                  />
                  <Button variant="outline" disabled={busy} onClick={() => srtRef.current?.click()}>
                    {t('production.srt')}
                  </Button>
                  {plan.subtitle_mode === 'provided' && (
                    <span className="text-xs text-muted-foreground">
                      {srtName} · {plan.captions.length}
                    </span>
                  )}
                </div>
                <label className="grid gap-2 text-sm">
                  {t('production.coverTime')}
                  <Input
                    type="number"
                    min="0"
                    step="0.1"
                    max={source ? Math.max(0, source.duration - 0.04) : undefined}
                    value={plan.cover_time}
                    disabled={busy}
                    onChange={(e) => update('cover_time', Number(e.target.value))}
                  />
                </label>
                <p className="text-xs text-muted-foreground">1080 × 1920 · 30 fps · CPU</p>
                <Button
                  disabled={
                    busy ||
                    !source ||
                    !outro ||
                    !plan.title.trim() ||
                    !plan.cover_title.trim() ||
                    !plan.publication_title.trim()
                  }
                  onClick={() => void submit()}
                >
                  {t('production.generate')}
                </Button>
              </section>
            </div>
          )}
          {tab === 'library' && (
            <section className="grid gap-4">
              <div className="flex gap-3">
                <Input
                  aria-label={t('common.search')}
                  placeholder={t('common.search')}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                <Button disabled={busy} onClick={() => chooseUpload('source_id')}>
                  <UploadIcon />
                  {t('gallery.upload')}
                </Button>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {(assets.data ?? [])
                  .filter((a) => a.filename.toLowerCase().includes(search.toLowerCase()))
                  .map((a) => (
                    <article key={a.id} className="grid gap-3 rounded-xl border border-border p-4">
                      {preview(a)}
                      <p className="truncate text-sm font-medium" title={a.filename}>
                        {a.filename}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {a.width} × {a.height} · {a.duration.toFixed(1)}s ·{' '}
                        {(a.bytes / 1024 ** 2).toFixed(1)} MiB
                      </p>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => {
                            chooseSource(a);
                            setTab('work');
                          }}
                        >
                          {t('production.source')}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy}
                          onClick={() => {
                            update('outro_id', a.id);
                            setAiResult(null);
                            setTab('work');
                          }}
                        >
                          {t('production.outro')}
                        </Button>
                      </div>
                    </article>
                  ))}
              </div>
            </section>
          )}
          {tab !== 'library' && (
            <section className="grid gap-4">
              <h2 className="font-medium">
                {t(tab === 'deliveries' ? 'production.deliveries' : 'avatar.queue')}
              </h2>
              {!tasks.length && (
                <p className="text-sm text-muted-foreground">{t('production.noRuns')}</p>
              )}
              {tasks.map((run) => (
                <article key={run.id} className="grid gap-3 rounded-xl border border-border p-4">
                  <div className="flex flex-wrap justify-between gap-2">
                    <h3 className="font-medium">{run.plan.title}</h3>
                    <span className="text-sm">{stateLabel(run.status)}</span>
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                    {Object.entries(run.steps).map(([key, status]) => (
                      <span key={key}>
                        {t('production.steps.' + key)} · {stateLabel(status)}
                      </span>
                    ))}
                  </div>
                  {run.error && (
                    <p className="whitespace-pre-wrap break-words text-sm text-destructive">
                      {run.error}
                    </p>
                  )}
                  {run.status === 'done' && (
                    <div className="grid items-start gap-3 sm:grid-cols-[2fr_1fr]">
                      <video
                        src={apiPath(prefix + '/runs/' + run.id + '/files/video')}
                        controls
                        playsInline
                        preload="metadata"
                        className="mx-auto aspect-[9/16] h-96 w-auto max-w-full rounded bg-black object-contain"
                      />
                      <img
                        src={apiPath(prefix + '/runs/' + run.id + '/files/cover')}
                        alt={run.plan.cover_title}
                        loading="lazy"
                        className="mx-auto aspect-[3/4] h-72 w-auto max-w-full rounded bg-black object-contain"
                      />
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2">
                    {run.status === 'done' && (
                      <Button size="sm" disabled={busy} onClick={() => void download(run)}>
                        <DownloadIcon />
                        {t('production.download')}
                      </Button>
                    )}
                    {['pending', 'running'].includes(run.status) ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => void jobAction(run.id, 'cancel')}
                      >
                        {t('common.cancel')}
                      </Button>
                    ) : (
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={() => {
                            setPlan({ ...emptyPlan, ...run.plan });
                            setSrtName('');
                            setTab('work');
                            setAiResult(null);
                            mainRef.current?.scrollTo?.({ top: 0, behavior: 'smooth' });
                          }}
                        >
                          {t('production.edit')}
                        </Button>
                        {run.status !== 'done' && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busy}
                            onClick={() => void jobAction(run.id, 'retry')}
                          >
                            {t('common.retry')}
                          </Button>
                        )}
                      </>
                    )}
                  </div>
                </article>
              ))}
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
