import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { ProductionPage } from './production-page';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', () => ({ apiPath: (path: string) => '/api' + path, apiJson: vi.fn(async () => ({url:'/native-harness/?token=fixture'})), describeError: String }));
vi.mock('@/components/app-shell/workspace-header', () => ({ WorkspaceHeader: ({children}: {children: React.ReactNode}) => <header>{children}</header> }));
it('embeds the native page and refreshes without custom product/chat forms', async () => {
 render(<QueryClientProvider client={new QueryClient()}><ProductionPage /></QueryClientProvider>);
 const frame = await screen.findByTitle('production.harnessBrand');
 expect(frame.getAttribute('src')).toBe('/api/native-harness/?token=fixture');
 expect(screen.queryByRole('textbox')).toBeNull();
 fireEvent.click(screen.getByRole('button', {name:'common.refresh'}));
 await waitFor(() => expect(screen.getByTitle('production.harnessBrand')).not.toBe(frame));
});
