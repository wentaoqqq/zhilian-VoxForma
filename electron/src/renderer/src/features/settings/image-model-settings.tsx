import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { DownloadIcon, ImageIcon } from 'lucide-react';
import { apiJson, describeError } from '@/lib/api/client';
import { Button } from '@/components/ui/button';
import { imageInstallKey, imageModelsKey, loadImageInstall } from '@/features/images/image-api';
import { SettingsSection } from './settings-layout';
import { ActiveBadge } from './model-list/badges';

export function ImageModelSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const install = useQuery({
    queryKey: imageInstallKey,
    queryFn: loadImageInstall,
    refetchInterval: (query) => query.state.data?.job?.state === 'running' ? 1000 : 5000,
  });
  const start = useMutation({
    mutationFn: () => apiJson('/image-generation/install', { method: 'POST' }),
    onSuccess: async () => {
      await install.refetch();
      await client.invalidateQueries({ queryKey: imageModelsKey });
    },
  });
  const state = install.data;
  const job = state?.job;
  const running = start.isPending || job?.state === 'running';
  return (
    <SettingsSection icon={ImageIcon} title={t('engineSidebar.image')}>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <strong>Diffusers · {state?.models.map((model) => model.label).join(', ') || 'Qwen Image 2.1 BF16'}</strong>
          {state?.installed && <ActiveBadge />}
        </div>
        <p className="text-sm text-muted-foreground">{t('imageGeneration.installDescription')}</p>
        {state && !state.installed && <p className="text-sm text-muted-foreground">
          {t('imageGeneration.weightSize', { size: (state.weight_bytes / 1024 ** 3).toFixed(1) })}
        </p>}
        {state?.supported === false && <p className="text-sm text-muted-foreground">{t('imageGeneration.requirements')}</p>}
        <Button onClick={() => start.mutate()} disabled={running || !state?.supported || state.installed || install.isError}>
          <DownloadIcon />{t(state?.installed ? 'videoEngine.installed' : job?.state === 'failed' ? 'videoEngine.retryInstall' : 'imageGeneration.install')}
        </Button>
        {running && <div role="status" className="space-y-1 text-sm">
          <p>{t('imageGeneration.installSteps.' + (job?.step || 'runtime'))}</p>
          {job?.filename && <p className="break-all text-xs text-muted-foreground">{job.filename}</p>}
          {!!job?.file_bytes && !!job.received_bytes && <progress className="w-full" max={job.file_bytes} value={job.received_bytes} aria-label={job.filename ?? undefined} />}
        </div>}
        {(install.error || start.error || job?.error) &&
          <p role="alert" className="break-words text-sm text-destructive">{job?.error || describeError(install.error || start.error)}</p>}
      </div>
    </SettingsSection>
  );
}
