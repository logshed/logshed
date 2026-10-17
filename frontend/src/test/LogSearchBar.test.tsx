import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { LogSearchBar } from '../components/logs/LogSearchBar.tsx';
import { LogFilterParams } from '../types.ts';
import * as savedViewsApi from '../api/savedViews.ts';

describe('LogSearchBar Component (Items #6, #22, #31)', () => {
  it('hides the Reset button when no filters are active', () => {
    const filters: LogFilterParams = {};
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        availableSources={['host-a', 'host-b']}
        availableApps={['nginx', 'postgres']}
      />
    );

    expect(screen.queryByTitle('Reset all active filters')).toBeNull();
  });

  it('shows the prominent Reset button with active count when query is present', () => {
    const filters: LogFilterParams = { query: 'timeout' };
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        availableSources={['host-a', 'host-b']}
        availableApps={['nginx', 'postgres']}
      />
    );

    const resetBtn = screen.getByTitle('Reset all active filters');
    expect(resetBtn).toBeInTheDocument();
    expect(resetBtn).toHaveTextContent('Reset');
    expect(resetBtn).toHaveTextContent('1'); // Badge count = 1
  });

  it('shows the prominent Reset button with aggregated count for multi-select hosts and apps', () => {
    const filters: LogFilterParams = {
      query: 'error',
      severity_max: 3,
      sources: ['pve-node1', 'pve-node2'],
      apps: ['corosync', 'pvedaemon', 'kernel'],
    };
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        availableSources={['pve-node1', 'pve-node2', 'docker']}
        availableApps={['corosync', 'pvedaemon', 'kernel', 'nginx']}
      />
    );

    const resetBtn = screen.getByTitle('Reset all active filters');
    expect(resetBtn).toBeInTheDocument();
    // query(1) + severity_max(1) + sources(2) + apps(3) = 7
    expect(resetBtn).toHaveTextContent('7');
  });

  it('calls onReset and resets time preset when clicking Reset button', () => {
    const handleReset = vi.fn();
    const filters: LogFilterParams = { query: 'panic' };
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={handleReset}
        availableSources={['host-a']}
        availableApps={['kernel']}
      />
    );

    const resetBtn = screen.getByTitle('Reset all active filters');
    fireEvent.click(resetBtn);

    expect(handleReset).toHaveBeenCalledTimes(1);
  });

  it('renders Host/IP and App/Container multi-select dropdowns on the filter row', () => {
    const filters: LogFilterParams = {};
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        availableSources={['pve-node1', 'homelab-srv']}
        availableApps={['pveproxy', 'pvedaemon']}
      />
    );

    expect(screen.getByText('Host / IP:')).toBeInTheDocument();
    expect(screen.getByText('All Hosts')).toBeInTheDocument();
    expect(screen.getByText('App / Container:')).toBeInTheDocument();
    expect(screen.getByText('All Apps')).toBeInTheDocument();
    expect(screen.getByText('Severity:')).toBeInTheDocument();
    expect(screen.getByText('Time:')).toBeInTheDocument();
  });

  it('allows opening multi-select dropdown and selecting multiple options', () => {
    const handleFilterChange = vi.fn();
    const filters: LogFilterParams = {};
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={handleFilterChange}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        availableSources={['pve-node1', 'pve-node2', 'homelab-nas']}
        availableApps={['pvedaemon', 'corosync']}
      />
    );

    // Click Host dropdown trigger
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    fireEvent.click(hostTrigger);

    // Popover should be visible with options
    expect(screen.getByText('pve-node1')).toBeInTheDocument();
    expect(screen.getByText('pve-node2')).toBeInTheDocument();
    expect(screen.getByText('homelab-nas')).toBeInTheDocument();

    // Select pve-node1
    fireEvent.click(screen.getByText('pve-node1'));
    expect(handleFilterChange).toHaveBeenCalledWith(
      expect.objectContaining({
        sources: ['pve-node1'],
        source: 'pve-node1',
      })
    );
  });

  it('does not display count indicator when no filters are active', () => {
    const filters: LogFilterParams = {};
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        totalCount={1001}
        totalCapped={true}
      />
    );

    expect(screen.queryByTestId('search-bar-total-count')).toBeNull();
    expect(screen.queryByText('1,000+ logs')).toBeNull();
  });

  it('displays "1,000+ logs" next to Filter button when filter is active and totalCapped is true rather than literal 1,001', () => {
    const filters: LogFilterParams = { query: 'timeout' };
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        totalCount={1001}
        totalCapped={true}
      />
    );

    expect(screen.getByText('1,000+ logs')).toBeInTheDocument();
    expect(screen.queryByText('1,001')).toBeNull();
    expect(screen.queryByText('1,001 logs')).toBeNull();
  });

  it('displays exact log count next to Filter button when filter is active and totalCapped is false', () => {
    const filters: LogFilterParams = { query: 'error' };
    render(
      <LogSearchBar
        filters={filters}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        totalCount={42}
        totalCapped={false}
      />
    );

    expect(screen.getByText('42 logs')).toBeInTheDocument();
  });
});

describe('LogSearchBar B11 fixes', () => {
  it('calls onSearch without arguments when the Filter button is clicked (B-01)', () => {
    const onSearch = vi.fn();
    render(
      <LogSearchBar filters={{ severity_max: 3 }} onFilterChange={vi.fn()} onSearch={onSearch} onReset={vi.fn()} />
    );
    fireEvent.click(screen.getByText('Filter').closest('button') as HTMLElement);
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch.mock.calls[0]).toHaveLength(0);
  });

  it('shows an inline error when deleting a saved view fails (B-47)', async () => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([
      { id: 1, name: 'Errors', query_params: {}, is_pinned: false, created_at: '2026-09-18T10:00:00Z' },
    ] as any);
    vi.spyOn(savedViewsApi, 'deleteSavedView').mockRejectedValue(new Error('Delete failed on server'));
    render(<LogSearchBar filters={{}} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: /Saved views menu/i }));
    fireEvent.click(await screen.findByLabelText('Delete view Errors'));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete View' }));
    expect(await screen.findByText('Delete failed on server')).toBeInTheDocument();
  });

  it('shows an inline error when pinning a saved view fails (B-47)', async () => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([
      { id: 1, name: 'Errors', query_params: {}, is_pinned: false, created_at: '2026-09-18T10:00:00Z' },
    ] as any);
    vi.spyOn(savedViewsApi, 'updateSavedView').mockRejectedValue(new Error('Pin failed on server'));
    render(<LogSearchBar filters={{}} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: /Saved views menu/i }));
    fireEvent.click(await screen.findByLabelText('Pin view Errors'));
    await waitFor(() => expect(screen.getByText('Pin failed on server')).toBeInTheDocument());
  });
});

describe('LogSearchBar time presets (B-23)', () => {
  const renderBar = (filters: LogFilterParams, onFilterChange = vi.fn()) => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
    render(<LogSearchBar filters={filters} onFilterChange={onFilterChange} onSearch={vi.fn()} onReset={vi.fn()} />);
    return onFilterChange;
  };
  const desktopTimeSelect = () => screen.getByText('Time:').parentElement!.querySelector('select') as HTMLSelectElement;

  it('stores a preset as a relative value instead of a fixed timestamp', () => {
    const onFilterChange = renderBar({ query: 'x' });
    fireEvent.change(desktopTimeSelect(), { target: { value: '1h' } });
    expect(onFilterChange).toHaveBeenCalledWith({ query: 'x', from: '1h', to: undefined });
  });

  it('shows the preset that matches a relative from value (saved view or link)', () => {
    renderBar({ from: '24h' });
    expect(desktopTimeSelect().value).toBe('24h');
  });

  it('shows Custom with the date pickers for an absolute from value', () => {
    renderBar({ from: '2026-10-13T10:00:00.000Z' });
    expect(desktopTimeSelect().value).toBe('custom');
    expect(screen.getAllByText('From:').length).toBeGreaterThan(0);
  });

  it('shows All Time when no time bounds are set', () => {
    renderBar({});
    expect(desktopTimeSelect().value).toBe('all');
  });

  it('turns a relative preset into an absolute start when switching to Custom', () => {
    const onFilterChange = renderBar({ from: '1h' });
    const before = Date.now();
    fireEvent.change(desktopTimeSelect(), { target: { value: 'custom' } });
    const call = onFilterChange.mock.calls[0][0];
    const from = new Date(call.from).getTime();
    expect(from).toBeGreaterThanOrEqual(before - 3600_000 - 1000);
    expect(from).toBeLessThanOrEqual(Date.now() - 3600_000 + 1000);
    expect(desktopTimeSelect().value).toBe('custom');
  });
});
