import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TopSourcesCard } from '../components/storage/TopSourcesCard.tsx';
import * as systemApi from '../api/system.ts';
import * as settingsApi from '../api/settings.ts';
import { TopSources } from '../types.ts';

const FIGURES: TopSources = {
  as_of: '2026-11-15T12:05:00+00:00',
  since: '2026-11-14T13:00:00+00:00',
  total: 20_000,
  hosts: [
    { name: 'truenas', count: 15_000 },
    { name: 'pve-node1', count: 150 },
  ],
  apps: [{ name: 'nginx', count: 9_000 }],
};

describe('Top sources card', () => {
  beforeEach(() => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ server_timezone: 'UTC' } as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.history.pushState(null, '', '/');
  });

  it('lists the top hosts and apps with their counts and share of all logs', async () => {
    vi.spyOn(systemApi, 'fetchTopSources').mockResolvedValue(FIGURES);
    render(<TopSourcesCard />);

    const hosts = await screen.findByRole('list', { name: 'Hosts' });
    const [truenas, pve] = within(hosts).getAllByRole('listitem');
    expect(within(truenas).getByText('truenas')).toBeInTheDocument();
    expect(within(truenas).getByText((15_000).toLocaleString())).toBeInTheDocument();
    expect(within(truenas).getByText('75%')).toBeInTheDocument();
    expect(within(pve).getByText('0.8%')).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Apps' })).getByText('45%')).toBeInTheDocument();
    expect(
      screen.getByText(/20,000 logs since 2026-11-14 13:00, by each log's own time\. Updated every 5 minutes, last at 2026-11-15 12:05\./)
    ).toBeInTheDocument();
  });

  it('Filter in Console opens the Console on that host from the start of the card window', async () => {
    vi.spyOn(systemApi, 'fetchTopSources').mockResolvedValue(FIGURES);
    const popstate = vi.fn();
    window.addEventListener('popstate', popstate);
    render(<TopSourcesCard />);

    fireEvent.click(await screen.findByRole('button', { name: 'Show the logs of truenas in the Console' }));
    window.removeEventListener('popstate', popstate);

    expect(window.location.pathname + window.location.search).toBe(`/?source=truenas&time=${encodeURIComponent('2026-11-14T13:00:00+00:00')}`);
    expect(popstate).toHaveBeenCalled();
  });

  it('Create Drop Rule opens the drop rule dialog for that app', async () => {
    vi.spyOn(systemApi, 'fetchTopSources').mockResolvedValue(FIGURES);
    render(<TopSourcesCard />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create a drop rule for nginx' }));

    expect(await screen.findByText('Create Ingestion Drop Rule')).toBeInTheDocument();
    expect(await screen.findByDisplayValue('Drop nginx Chatter')).toBeInTheDocument();
  });

  it('says when the server has not worked the figures out yet, or no logs arrived', async () => {
    const spy = vi.spyOn(systemApi, 'fetchTopSources').mockResolvedValue({ as_of: null, since: null, total: 0, hosts: [], apps: [] });
    const { unmount } = render(<TopSourcesCard />);
    expect(await screen.findByText(/working out the figures/)).toBeInTheDocument();
    unmount();

    spy.mockResolvedValue({ ...FIGURES, total: 0, hosts: [], apps: [] });
    render(<TopSourcesCard />);
    expect(await screen.findByText('No logs in the last 24 hours.')).toBeInTheDocument();
  });
});
