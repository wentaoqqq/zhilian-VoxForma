import { useEffect, useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { DownloadIcon, ImageIcon, SparklesIcon } from 'lucide-react';
// @ts-expect-error Shared legacy JSX control has no declaration file.
import SearchableSelect from '@shared/components/SearchableSelect';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button, buttonVariants } from '@/components/ui/button';
import { apiFetch, apiJson, describeError } from '@/lib/api/client';
import { saveLocalFile } from '@/lib/local-export';
import { cn } from '@/lib/utils';
import { currentImageJob, imageJobsKey, imageModelsKey, loadImageJobs, loadImageModels, type ImageJob, type ImageMode } from './image-api';

const modes = ['text-to-image', 'image-to-image', 'edit'] as const;

const sizes = [
  '1024x1024', '1024x576', '576x1024', '2048x2048', '2048x1152', '1152x2048',
].map((value) => ({ value, label: value.replace('x', ' × ') }));

function usePreview(blob: Blob | undefined) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!blob) {
      setUrl('');
      return;
    }
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);
  return url;
}

function ReferenceImage({ file, index, onRemove }: { file: File; index: number; onRemove: () => void }) {
  const { t } = useTranslation();
  const url = usePreview(file);
  return (
    <div className="flex items-center gap-3">
      {url && <img src={url} alt="" className="size-12 rounded-md border object-contain" />}
      <p className="min-w-0 flex-1 break-all text-sm">{index + 1}. {file.name}</p>
      <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
        {t('imageGeneration.removeReference')}
      </Button>
    </div>
  );
}

export function ImagePage() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [mode, setMode] = useState<ImageMode>('text-to-image');
  const [prompt, setPrompt] = useState('');
  const [size, setSize] = useState('1024x1024');
  const [references, setReferences] = useState<File[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [resultError, setResultError] = useState('');
  const needsReference = mode !== 'text-to-image';
  const models = useQuery({ queryKey: imageModelsKey, queryFn: loadImageModels, refetchInterval: 5000 });
  const maxReferences = models.data?.max_references ?? 1;
  const jobs = useQuery({ queryKey: imageJobsKey, queryFn: loadImageJobs, refetchInterval: 2000 });
  const canSubmit = models.data?.installed && !submitting && !!prompt.trim() && (!needsReference || references.length > 0);
  const rows = jobs.data?.jobs ?? [];
  const selected = rows.find((job) => job.id === selectedId) ?? rows[0];
  const result = useQuery({
    queryKey: ['image-result', selected?.id],
    enabled: selected?.state === 'done',
    queryFn: async ({ signal }) => (await apiFetch('/image-generation/jobs/' + selected?.id + '/image', { signal })).blob(),
    staleTime: Infinity,
  });
  const resultUrl = usePreview(result.data);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError('');
    setResultError('');
    try {
      const data = new FormData();
      data.set('prompt', prompt.trim());
      data.set('mode', mode);
      data.set('size', mode === 'edit' ? 'reference' : size);
      if (needsReference) {
        for (const file of references) data.append('reference', file);
      }
      const job = await apiJson<ImageJob>('/image-generation/jobs', { method: 'POST', body: data });
      client.setQueryData<{ jobs: ImageJob[] }>(imageJobsKey, (old) => ({
        jobs: [job, ...(old?.jobs ?? []).filter((existing) => job.id !== existing.id)],
      }));
      setSelectedId(job.id);
      await client.invalidateQueries({ queryKey: imageJobsKey });
    } catch (cause) {
      setError(describeError(cause));
    } finally {
      setSubmitting(false);
    }
  }

  async function download() {
    if (!result.data || !selected) return;
    setResultError('');
    try {
      await saveLocalFile(result.data, 'image-' + selected.id + '.png');
    } catch (cause) {
      setResultError(describeError(cause));
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader><h1>{t('engineSidebar.image')}</h1></WorkspaceHeader>
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto grid max-w-6xl gap-5 p-5 lg:grid-cols-[minmax(320px,1fr)_minmax(0,1.25fr)]">
          <form onSubmit={submit} className="space-y-4 self-start rounded-xl border bg-card p-5">
            {models.data && !models.data.installed && (
              <div className="space-y-2 rounded-lg border border-border p-3 text-sm">
                <p>{t(models.data.supported ? 'imageGeneration.installFirst' : 'imageGeneration.requirements')}</p>
                <Link to="/settings/models/$family" params={{ family: 'image' }} className={buttonVariants({ size: 'sm', variant: 'outline' })}>
                  {t('imageGeneration.openSettings')}
                </Link>
              </div>
            )}
            <div className="grid grid-cols-3 gap-1 rounded-lg border bg-muted/30 p-1">
              {modes.map((entry) => (
                <Button key={entry} type="button" size="sm" variant={entry === mode ? 'secondary' : 'ghost'}
                  aria-pressed={entry === mode} onClick={() => { setMode(entry); setError(''); }}
                  className="h-auto min-h-8 whitespace-normal px-2">
                  {t('imageGeneration.modes.' + entry)}
                </Button>
              ))}
            </div>
            <div className="grid gap-2">
              <label htmlFor="image-prompt" className="font-medium">{t('imageGeneration.prompt')}</label>
              <textarea id="image-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)}
                maxLength={8000} required className="min-h-32 resize-y rounded-md border bg-background p-3 text-sm" />
            </div>
            {needsReference && <div className="grid gap-2">
              <label htmlFor="image-references" className="flex justify-between text-sm font-medium">
                {t('imageGeneration.reference')} <span className="text-muted-foreground">{references.length} / {maxReferences}</span>
              </label>
              <input id="image-references" type="file" multiple={maxReferences > 1}
                disabled={references.length >= maxReferences}
                accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []);
                  e.target.value = '';
                  if (references.length + files.length > maxReferences) {
                    setError(t('imageGeneration.referenceLimit', { count: maxReferences }));
                    return;
                  }
                  setError('');
                  setReferences((current) => [...current, ...files]);
                }}
                className="w-full text-sm" />
              {references.map((file, index) => (
                <ReferenceImage key={file.name + file.lastModified + index} file={file} index={index} onRemove={() => {
                  setReferences((current) => current.filter((_, position) => position !== index));
                  setError('');
                }} />
              ))}
            </div>}
            <div className="grid min-w-0 gap-2">
              <span className="text-sm font-medium">{t('imageGeneration.size')}</span>
              {mode === 'edit'
                ? <p className="py-1 text-sm text-muted-foreground">{t('imageGeneration.referenceSize')}</p>
                : <SearchableSelect value={size} onChange={setSize} options={sizes} ariaLabel={t('imageGeneration.size')} menuPortal />}
            </div>
            <Button type="submit" className="w-full" disabled={!canSubmit}>
              <SparklesIcon />{t('imageGeneration.generate')}
            </Button>
            {(error || models.error) && <p role="alert" className="text-sm text-destructive">{error || describeError(models.error)}</p>}
          </form>
          <section className="min-w-0 space-y-4">
            <div className="grid min-h-64 place-items-center gap-3 rounded-xl border bg-card p-4">
              {resultUrl && selected?.state === 'done'
                ? <img src={resultUrl} alt={selected.prompt} className="max-h-[65vh] max-w-full rounded-lg object-contain" />
                : <div className="text-center text-sm text-muted-foreground"><ImageIcon className="mx-auto mb-2 size-9" />
                  {t(selected ? 'imageGeneration.states.' + selected.state : 'imageGeneration.empty')}
                </div>}
              {selected?.state === 'done' && result.data && <Button variant="outline" onClick={() => void download()}><DownloadIcon />{t('imageGeneration.download')}</Button>}
              {(selected?.error || result.error || resultError) && <p role="alert" className="max-w-full break-words text-sm text-destructive">{selected?.error || resultError || describeError(result.error)}</p>}
            </div>
            {jobs.error && <p role="alert" className="text-sm text-destructive">{describeError(jobs.error)}</p>}
            {rows.length > 0 && <div className="space-y-2 rounded-xl border bg-card p-4">
              <h2 className="text-sm font-medium">{t('imageGeneration.history')}</h2>
              <div className="max-h-64 space-y-1 overflow-auto">
                {rows.map((job) => <button key={job.id} type="button" aria-pressed={selected?.id === job.id}
                  onClick={() => { setSelectedId(job.id); setResultError(''); }}
                  className={cn('flex w-full items-center justify-between gap-3 rounded-md px-2 py-2 text-left text-sm hover:bg-muted', selected?.id === job.id && 'bg-muted')}>
                  <span className="min-w-0 truncate">
                    <span className="mr-2 text-xs text-muted-foreground">
                      {t('imageGeneration.modes.' + (job.mode ?? (job.size === 'reference' ? 'edit' : 'text-to-image')))}
                    </span>
                    {job.prompt}
                  </span>
                  <span className={cn('shrink-0 text-xs text-muted-foreground', currentImageJob(job) && 'animate-pulse')}>{t('imageGeneration.states.' + job.state)}</span>
                </button>)}
              </div>
            </div>}
          </section>
        </div>
      </div>
    </div>
  );
}
