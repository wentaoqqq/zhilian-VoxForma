import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button } from '@/components/ui/button';
import { apiJson, apiPath, describeError } from '@/lib/api/client';

export function ProductionPage() {
  const { t } = useTranslation();
  const entry = useQuery({
    queryKey: ['native-harness-entry'],
    queryFn: () => apiJson<{ url: string }>('/production/harness'),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="production-page">
      <WorkspaceHeader>
        <h1>{t('production.title')} · DeepSeek Harness</h1>
        <Button variant="ghost" size="sm" onClick={() => void entry.refetch()}>
          {t('common.refresh')}
        </Button>
      </WorkspaceHeader>
      {entry.error && (
        <p role="alert" className="p-4">{describeError(entry.error)}</p>
      )}
      {entry.data && (
        <iframe
          key={entry.dataUpdatedAt}
          title="DeepSeek Harness"
          src={apiPath(entry.data.url)}
          className="min-h-0 w-full flex-1 border-0"
          allow="clipboard-read; clipboard-write"
          referrerPolicy="no-referrer"
        />
      )}
    </div>
  );
}
