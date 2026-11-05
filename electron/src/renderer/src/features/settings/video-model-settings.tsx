import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { VideoIcon, DownloadIcon } from 'lucide-react';
import { apiJson, describeError } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { SettingsSection } from './settings-layout';

type VideoState = {
  backends: Array<{
    id: string; display_name: string; available: boolean;
    runtime_installed: boolean; source_installed: boolean;
    models: Array<{ repo_id: string; label: string; installed: boolean; size_gb: number; download_size_bytes: number }>;
  }>;
};
type InstallState = {
  supported: boolean; reason: string; installed: boolean;
  job: null | { state: string; step: string; error: string | null; log: string[];
    progress: null | { filename: string; downloaded: number; total: number; bytes_per_second: number } };
};

export function VideoModelSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({ queryKey: ['video-engines'], queryFn: () => apiJson<VideoState>('/engines/video'), refetchInterval: 5000 });
  const install = useQuery({ queryKey: ['video-install'], queryFn: () => apiJson<InstallState>('/engines/video/install/status'),
    refetchInterval: q => q.state.data?.job?.state === 'running' ? 1000 : 5000 });
  const job = install.data?.job;
  const running = starting || job?.state === 'running';
  const progress = job?.progress;
  const start = async (component: 'student' | 'rife') => {
    setStarting(true); setError(null);
    try {
      await apiJson('/engines/video/install', { method: 'POST', body: JSON.stringify({ component }) });
      await Promise.all([install.refetch(), query.refetch()]);
      await client.invalidateQueries({ queryKey: ['engines'] });
    } catch (e) { setError(describeError(e)); }
    finally { setStarting(false); }
  };
  return <SettingsSection icon={VideoIcon} title={t('videoEngine.title')}>
    {query.isLoading && <p className="p-4">{t('videoEngine.loading')}</p>}
    {query.isError && <p className="p-4 text-destructive">{t('videoEngine.error')}</p>}
    {query.data?.backends.map(engine => <div key={engine.id} className="space-y-4 p-4">
      <div className="flex items-center justify-between"><strong>{engine.display_name}</strong><span>{t(engine.available ? 'videoEngine.ready' : 'videoEngine.incomplete')}</span></div>
      <p className="text-sm text-muted-foreground">{t('videoEngine.description')}</p>
      <p className="text-sm text-muted-foreground">{t('videoEngine.installHint')}</p>
      <p className="text-xs text-muted-foreground">{t('videoEngine.requirements')}</p>
      <div className="text-sm">{t('videoEngine.runtime')}: {t(engine.runtime_installed && engine.source_installed ? 'videoEngine.ready' : 'videoEngine.incomplete')}</div>
      <div className="divide-y rounded-lg border">{engine.models.map(model => <div key={model.repo_id} className="flex justify-between gap-4 p-3 text-sm"><span>{model.label}</span><span>{t(model.installed ? 'videoEngine.installed' : 'videoEngine.missing')} · {((model.installed ? model.size_gb : model.download_size_bytes / 1024 ** 3)).toFixed(2)} GiB</span></div>)}</div>
      <div className="flex flex-wrap gap-2">
        <Button disabled={running || engine.available || !install.data?.supported || install.isError} onClick={() => void start('student')}><DownloadIcon />{t(engine.available ? 'videoEngine.installed' : job && ['failed','cancelled','interrupted'].includes(job.state) ? 'videoEngine.retryInstall' : 'videoEngine.installStudent')}</Button>
        {engine.available && !engine.models.find(m => m.repo_id === 'local/tbdub-rife')?.installed && <Button variant="outline" disabled={running || !install.data?.supported || install.isError} onClick={() => void start('rife')}>{t('videoEngine.installRife')}</Button>}
        {running && <Button variant="outline" onClick={async () => {
          try { await apiJson('/engines/video/install/cancel', { method: 'POST' }); await install.refetch(); }
          catch (e) { setError(describeError(e)); }
        }}>{t('videoEngine.cancelInstall')}</Button>}
      </div>
      {install.data?.supported === false && <p className="text-sm text-destructive">{install.data.reason}</p>}
      {running && <p role="status" className="text-sm">{t('modelMaintenance.installing')} · {t('videoEngine.steps.' + (job?.step || 'preflight'))}</p>}
      {running && progress && <div className="space-y-1 text-sm"><p>{progress.filename}</p><progress className="h-2 w-full" aria-label={progress.filename} max={progress.total} value={progress.downloaded} /><p>{(progress.downloaded / 1024 ** 3).toFixed(2)} / {(progress.total / 1024 ** 3).toFixed(2)} GiB · {(progress.bytes_per_second / 1024 ** 2).toFixed(1)} MiB/s</p></div>}
      {(error || install.isError || job?.state === 'failed' || job?.state === 'interrupted') && <p role="alert" className="whitespace-pre-wrap text-sm text-destructive">{error || (install.isError ? describeError(install.error) : job?.error)}</p>}
      {job && <details className="text-xs"><summary className="cursor-pointer">{t('videoEngine.installLogs')}</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded border p-3">{job.log.join('\n')}</pre></details>}
      <p className="text-xs text-muted-foreground">{t('videoEngine.lifecycle')}</p>
    </div>)}
    <div className="p-4 pt-0"><Button variant="outline" disabled={query.isFetching} onClick={() => { void query.refetch(); void install.refetch(); }}>{t('videoEngine.refresh')}</Button></div>
  </SettingsSection>;
}
