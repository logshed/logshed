import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HostAliasManager } from '../components/aliases/HostAliasManager.tsx';
import { DockerHostsTab } from '../components/settings/DockerHostsTab.tsx';
import * as aliasesApi from '../api/aliases.ts';
import * as dockerHostsApi from '../api/dockerHosts.ts';
import * as settingsApi from '../api/settings.ts';
import { AliasContext } from '../context/AliasContext.tsx';
import { formatAgo } from '../utils/formatters.ts';
import { DockerEndpoint, HostAlias } from '../types.ts';

const NOW = new Date('2026-11-15T12:00:00Z');

describe('formatAgo', () => {
  const ago = (seconds: number) => formatAgo(new Date(NOW.getTime() - seconds * 1000).toISOString(), NOW.getTime());

  it('names the age in the largest whole unit', () => {
    expect(ago(2)).toBe('just now');
    expect(ago(12)).toBe('12s ago');
    expect(ago(59)).toBe('59s ago');
    expect(ago(60)).toBe('1 min ago');
    expect(ago(3599)).toBe('59 min ago');
    expect(ago(3600)).toBe('1 h ago');
    expect(ago(47 * 3600)).toBe('47 h ago');
    expect(ago(3 * 86400)).toBe('3 days ago');
  });

  it('reads a time a little ahead of the browser clock as just now', () => {
    expect(ago(-30)).toBe('just now');
  });
});

const aliasRows = (seen: string | null): HostAlias[] => [
  { ip: '192.168.1.1', alias: 'router.local', notes: null, created_at: '2026-09-01T10:00:00Z', last_seen: seen },
  { ip: '192.168.1.9', alias: 'old-nas', notes: null, created_at: '2026-09-01T10:00:00Z', last_seen: null },
];

const renderAliases = () =>
  render(
    <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
      <HostAliasManager />
    </AliasContext.Provider>
  );

describe('Last seen on Settings -> Sources', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(NOW);
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ server_timezone: 'UTC' } as never);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('shows each alias row how long ago its address sent a log, or that it has not', async () => {
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue(aliasRows('2026-11-15T11:58:00Z'));
    renderAliases();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const recent = screen.getByText('2 min ago');
    expect(recent).toHaveAttribute('title', 'Last log received 2026-11-15 11:58:00');
    expect(screen.getByText('Not seen')).toBeInTheDocument();
  });

  it('fetches the times again every 30 seconds without resetting an open edit dialog', async () => {
    const fetchSpy = vi
      .spyOn(aliasesApi, 'fetchAliases')
      .mockResolvedValueOnce(aliasRows(null))
      .mockResolvedValue(aliasRows('2026-11-15T12:00:20Z'));
    renderAliases();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    fireEvent.click(screen.getByRole('button', { name: 'Edit alias router.local' }));
    const aliasBox = screen.getByLabelText('Friendly Host Alias');
    fireEvent.change(aliasBox, { target: { value: 'gateway' } });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('Friendly Host Alias')).toHaveValue('gateway');
    expect(screen.getAllByText('10s ago').length).toBeGreaterThan(0);
  });

  it('shows each Docker host row when its containers last sent a log', async () => {
    const hosts: DockerEndpoint[] = [
      {
        id: 1, name: 'Primary Docker', url: 'unix:///var/run/docker.sock', source_alias: 'docker',
        enabled: true, tls_enabled: false, exclude_containers: '', created_at: '2026-10-10T10:00:00Z',
        updated_at: '2026-10-10T10:00:00Z', is_connected: true, active_containers: 4,
        last_seen: '2026-11-15T09:00:00Z',
      },
    ];
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue(hosts);
    render(<DockerHostsTab />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const row = screen.getByText('Primary Docker').closest('tr') as HTMLElement;
    expect(within(row).getByText('3 h ago')).toBeInTheDocument();
  });
});
