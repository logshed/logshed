import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HostAliasManager } from '../components/aliases/HostAliasManager.tsx';
import * as aliasesApi from '../api/aliases.ts';
import { AliasContext } from '../context/AliasContext.tsx';
import { HostAlias, SelfReportedHosts } from '../types.ts';

const ALIASES: HostAlias[] = [
  { ip: '192.168.1.1', alias: 'router.local', notes: null, created_at: '2026-09-01T10:00:00Z', last_seen: null },
];

const REPORTED: SelfReportedHosts = {
  hosts: [{ ip: '172.17.0.4', names: ['hp-m404', 'printer-hp'], last_seen: new Date(Date.now() - 90_000).toISOString() }],
  truncated: false,
  limit: 200,
};

const renderManager = (bumpAliasVersion = vi.fn()) =>
  render(
    <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion }}>
      <HostAliasManager />
    </AliasContext.Provider>
  );

describe('Self-reported hosts on Settings -> Sources', () => {
  beforeEach(() => {
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue(ALIASES);
    vi.spyOn(aliasesApi, 'fetchSelfReportedHosts').mockResolvedValue(REPORTED);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('lists an address with no alias by its newest reported name, marked, with its other names on hover', async () => {
    renderManager();

    const row = await screen.findByTestId('self-reported-row');
    expect(within(row).getByText('172.17.0.4')).toBeInTheDocument();
    expect(within(row).getByText('hp-m404')).toBeInTheDocument();
    const badge = within(row).getByText('Self-reported +1');
    expect(badge).toHaveAttribute(
      'title',
      'The name 172.17.0.4 sends itself (no alias set). It has also sent: printer-hp'
    );
    expect(within(row).getByText('1 min ago')).toBeInTheDocument();
    expect(screen.getByText(/Active Host Mappings \(1, 1 self-reported\)/)).toBeInTheDocument();
  });

  it('Make it the alias saves the reported name for the address and reloads the list', async () => {
    const saveSpy = vi.spyOn(aliasesApi, 'saveAlias').mockResolvedValue({
      ip: '172.17.0.4', alias: 'hp-m404', created_at: '2026-11-15T12:00:00Z',
    });
    const bump = vi.fn();
    renderManager(bump);

    const row = await screen.findByTestId('self-reported-row');
    vi.mocked(aliasesApi.fetchSelfReportedHosts).mockResolvedValue({ ...REPORTED, hosts: [] });
    fireEvent.click(within(row).getByRole('button', { name: 'Make it the alias' }));

    await waitFor(() => expect(screen.queryByTestId('self-reported-row')).not.toBeInTheDocument());
    expect(saveSpy).toHaveBeenCalledWith({ ip: '172.17.0.4', alias: 'hp-m404' });
    expect(bump).toHaveBeenCalled();
  });

  it('shows why Make it the alias failed, and keeps the row', async () => {
    vi.spyOn(aliasesApi, 'saveAlias').mockRejectedValue(new Error("The alias 'hp-m404' is already used by a Docker host."));
    renderManager();

    const row = await screen.findByTestId('self-reported-row');
    fireEvent.click(within(row).getByRole('button', { name: 'Make it the alias' }));

    expect(await screen.findByText("The alias 'hp-m404' is already used by a Docker host.")).toBeInTheDocument();
    expect(screen.getByTestId('self-reported-row')).toBeInTheDocument();
  });

  it('the edit button opens Add Host Alias with the address and the name filled in', async () => {
    renderManager();

    const row = await screen.findByTestId('self-reported-row');
    fireEvent.click(within(row).getByRole('button', { name: 'Add an alias for 172.17.0.4' }));

    expect(screen.getByLabelText('Source IP Address')).toHaveValue('172.17.0.4');
    expect(screen.getByLabelText('Friendly Host Alias')).toHaveValue('hp-m404');
  });

  it('says when only the most recently seen self-reported hosts are listed', async () => {
    vi.mocked(aliasesApi.fetchSelfReportedHosts).mockResolvedValue({ ...REPORTED, truncated: true });
    renderManager();

    expect(await screen.findByText('Showing the 200 self-reported hosts seen most recently.')).toBeInTheDocument();
  });

  it('shows self-reported hosts when no alias is set yet', async () => {
    vi.mocked(aliasesApi.fetchAliases).mockResolvedValue([]);
    renderManager();

    expect(await screen.findByTestId('self-reported-row')).toBeInTheDocument();
    expect(screen.queryByText(/No host aliases mapped yet/)).not.toBeInTheDocument();
  });
});
