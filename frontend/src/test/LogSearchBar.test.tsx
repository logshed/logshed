import type { ReactElement } from 'react';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LogSearchBar } from '../components/logs/LogSearchBar.tsx';
import { LogFilterParams } from '../types.ts';
import * as savedViewsApi from '../api/savedViews.ts';

/**
 * Renders, then lets the saved views request the search bar starts on mount settle inside act, so
 * its state updates do not land after the test (BL-11).
 */
async function renderSettled(ui: ReactElement) {
  const result = render(ui);
  await act(async () => {});
  return result;
}

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
  it('calls onSearch without arguments when the Search now button is clicked (B-01)', () => {
    const onSearch = vi.fn();
    render(
      <LogSearchBar filters={{ severity_max: 3 }} onFilterChange={vi.fn()} onSearch={onSearch} onReset={vi.fn()} />
    );
    fireEvent.click(screen.getByLabelText('Search now'));
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
  const renderBar = async (filters: LogFilterParams, onFilterChange = vi.fn()) => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
    await renderSettled(<LogSearchBar filters={filters} onFilterChange={onFilterChange} onSearch={vi.fn()} onReset={vi.fn()} />);
    return onFilterChange;
  };
  const desktopTimeSelect = () => screen.getByText('Time:').parentElement!.querySelector('select') as HTMLSelectElement;

  it('stores a preset as a relative value instead of a fixed timestamp', async () => {
    const onFilterChange = await renderBar({ query: 'x' });
    fireEvent.change(desktopTimeSelect(), { target: { value: '1h' } });
    expect(onFilterChange).toHaveBeenCalledWith({ query: 'x', from: '1h', to: undefined });
  });

  it('shows the preset that matches a relative from value (saved view or link)', async () => {
    await renderBar({ from: '24h' });
    expect(desktopTimeSelect().value).toBe('24h');
  });

  it('shows Custom with the date pickers for an absolute from value', async () => {
    await renderBar({ from: '2026-10-13T10:00:00.000Z' });
    expect(desktopTimeSelect().value).toBe('custom');
    expect(screen.getAllByText('From:').length).toBeGreaterThan(0);
  });

  it('shows All Time when no time bounds are set', async () => {
    await renderBar({});
    expect(desktopTimeSelect().value).toBe('all');
  });

  it('turns a relative preset into an absolute start when switching to Custom', async () => {
    const onFilterChange = await renderBar({ from: '1h' });
    const before = Date.now();
    fireEvent.change(desktopTimeSelect(), { target: { value: 'custom' } });
    const call = onFilterChange.mock.calls[0][0];
    const from = new Date(call.from).getTime();
    expect(from).toBeGreaterThanOrEqual(before - 3600_000 - 1000);
    expect(from).toBeLessThanOrEqual(Date.now() - 3600_000 + 1000);
    expect(desktopTimeSelect().value).toBe('custom');
  });
});

describe('LogSearchBar N07 fixes', () => {
  const baseProps = {
    onSearch: vi.fn(),
    onReset: vi.fn(),
    availableSources: ['host-a'],
    availableApps: ['nginx'],
  };

  beforeEach(() => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
  });

  it('shows a note in the host and app lists when the facets were capped', async () => {
    await renderSettled(<LogSearchBar {...baseProps} filters={{}} onFilterChange={vi.fn()} facetsTruncated facetsLimit={500} />);
    fireEvent.click(screen.getByText('All Hosts'));
    expect(screen.getByText(/Showing the first 500 hosts/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('All Apps'));
    expect(screen.getByText(/Showing the first 500 apps/)).toBeInTheDocument();
  });

  it('shows no capped note when the facets were not capped', async () => {
    await renderSettled(<LogSearchBar {...baseProps} filters={{}} onFilterChange={vi.fn()} facetsTruncated={false} />);
    fireEvent.click(screen.getByText('All Hosts'));
    expect(screen.queryByText(/Showing the first 500/)).toBeNull();
  });

  it('offers a Today preset that sends since_midnight', async () => {
    const onFilterChange = vi.fn();
    await renderSettled(<LogSearchBar {...baseProps} filters={{}} onFilterChange={onFilterChange} />);
    const select = screen.getAllByRole('combobox').find((el) =>
      Array.from((el as HTMLSelectElement).options).some((o) => o.value === '15m')
    ) as HTMLSelectElement;
    expect(Array.from(select.options).some((o) => o.value === 'since_midnight' && o.text === 'Today')).toBe(true);
    fireEvent.change(select, { target: { value: 'since_midnight' } });
    expect(onFilterChange).toHaveBeenCalledWith(expect.objectContaining({ from: 'since_midnight' }));
  });

  it('shows Today for a saved view or link carrying since_midnight', async () => {
    await renderSettled(<LogSearchBar {...baseProps} filters={{ from: 'since_midnight' }} onFilterChange={vi.fn()} />);
    const select = screen.getAllByRole('combobox').find((el) =>
      Array.from((el as HTMLSelectElement).options).some((o) => o.value === '15m')
    ) as HTMLSelectElement;
    expect(select.value).toBe('since_midnight');
  });
});

describe('LogSearchBar severity wording (WP2)', () => {
  beforeEach(() => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
  });

  it('desktop select uses compact labels and a long title', async () => {
    await renderSettled(
      <LogSearchBar filters={{ severity_max: 4 }} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} />
    );
    const option = screen.getByRole('option', { name: 'Warn+ (0-4)' });
    const select = option.closest('select') as HTMLSelectElement;
    expect(select.title).toBe('Warning (4) and more severe');
    expect(screen.getByRole('option', { name: 'Debug+ (0-7)' })).toBeInTheDocument();
  });

  it('mobile drawer select uses the long "and more severe" labels', async () => {
    await renderSettled(
      <LogSearchBar filters={{}} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} />
    );
    fireEvent.click(screen.getByLabelText('Open filters drawer'));
    expect(screen.getByRole('option', { name: 'Warning (4) and more severe' })).toBeInTheDocument();
    expect(screen.queryByText(/Maximum Severity/)).toBeNull();
  });
});

describe('LogSearchBar search button, drawer and syntax help (WP5-2, WP5-3)', () => {
  const renderBar = async (filters: LogFilterParams = {}, onSearch = vi.fn()) => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
    await renderSettled(<LogSearchBar filters={filters} onFilterChange={vi.fn()} onSearch={onSearch} onReset={vi.fn()} />);
    return onSearch;
  };

  it('uses a secondary Search now icon button instead of the blue Filter button', async () => {
    const onSearch = await renderBar();
    const button = screen.getByRole('button', { name: 'Search now' });
    expect(button).toHaveAttribute('title', 'Search now (Enter)');
    expect(button.className).not.toContain('bg-accent-600');
    expect(screen.queryByText('Filter')).toBeNull();
    fireEvent.click(button);
    expect(onSearch).toHaveBeenCalledTimes(1);
  });

  it('mobile drawer closes and searches with Done', async () => {
    const onSearch = await renderBar();
    fireEvent.click(screen.getByLabelText('Open filters drawer'));
    expect(screen.queryByText('Apply Filters')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onSearch).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Done' })).toBeNull());
  });

  it('shows the new placeholder', async () => {
    await renderBar();
    expect(screen.getByPlaceholderText('Search logs, e.g. "disk full", error OR timeout, app:nginx')).toBeInTheDocument();
  });

  it('the help button opens a popover listing the search syntax', async () => {
    await renderBar();
    const help = screen.getByRole('button', { name: 'Search syntax help' });
    expect(help).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(help);
    expect(help).toHaveAttribute('aria-expanded', 'true');
    const popover = screen.getByRole('dialog', { name: 'Search syntax' });
    for (const example of ['"exact phrase"', 'error OR timeout', 'auth NOT cron', 'app:nginx', 'host:pve-node2', 'msg:"disk full"']) {
      expect(within(popover).getByText(example)).toBeInTheDocument();
    }
    expect(within(popover).getByText(/time finds timeout/)).toBeInTheDocument();
    expect(within(popover).getByText(/any case/)).toBeInTheDocument();
    fireEvent.click(help);
    expect(screen.queryByRole('dialog', { name: 'Search syntax' })).toBeNull();
  });

  it('the popover closes on Esc, on an outside click and when focus leaves it', async () => {
    await renderBar();
    const help = screen.getByRole('button', { name: 'Search syntax help' });

    fireEvent.click(help);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Search syntax' })).toBeNull();
    expect(help).toHaveAttribute('aria-expanded', 'false');
    expect(document.activeElement).toBe(help);

    fireEvent.click(help);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('dialog', { name: 'Search syntax' })).toBeNull();

    fireEvent.click(help);
    // Moving focus into the popover keeps it open; moving it elsewhere closes it
    fireEvent.blur(help, { relatedTarget: screen.getByRole('dialog', { name: 'Search syntax' }) });
    expect(screen.getByRole('dialog', { name: 'Search syntax' })).toBeInTheDocument();
    fireEvent.blur(help, { relatedTarget: screen.getByPlaceholderText(/Search logs/) });
    expect(screen.queryByRole('dialog', { name: 'Search syntax' })).toBeNull();
  });

  it('places the help button left of the clear button when there is a query', async () => {
    await renderBar({ query: 'disk' });
    const help = screen.getByRole('button', { name: 'Search syntax help' });
    const clear = screen.getByRole('button', { name: 'Clear search query' });
    expect(help.compareDocumentPosition(clear) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(help.parentElement!.className).toContain('right-7');
  });
});

describe('LogSearchBar capped-list note follows the server limit (BL-04)', () => {
  const baseProps = {
    onSearch: vi.fn(),
    onReset: vi.fn(),
    availableSources: ['host-a'],
    availableApps: ['nginx'],
  };

  beforeEach(() => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
  });

  it('names the limit the server reported', async () => {
    await renderSettled(<LogSearchBar {...baseProps} filters={{}} onFilterChange={vi.fn()} facetsTruncated facetsLimit={250} />);
    fireEvent.click(screen.getByText('All Hosts'));
    expect(screen.getByText('Showing the first 250 hosts. Type a name to add others.')).toBeInTheDocument();
    fireEvent.click(screen.getByText('All Apps'));
    expect(screen.getByText('Showing the first 250 apps. Type a name to add others.')).toBeInTheDocument();
  });

  it('does not guess a number when the limit is unknown', async () => {
    await renderSettled(<LogSearchBar {...baseProps} filters={{}} onFilterChange={vi.fn()} facetsTruncated />);
    fireEvent.click(screen.getByText('All Hosts'));
    expect(screen.getByText('Showing only some hosts. Type a name to add others.')).toBeInTheDocument();
  });
});
