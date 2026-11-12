import { useState, type ReactElement, type ReactNode } from 'react';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LogSearchBar } from '../components/logs/LogSearchBar.tsx';
import { LogFilterParams } from '../types.ts';
import * as savedViewsApi from '../api/savedViews.ts';
import { chooseOption, panelSelect, shownValue, toolbarSelect } from './selectDropdownHelpers.ts';
import { spacedName } from './accessibleNames.ts';

/**
 * Renders, then lets the saved views request the search bar starts on mount settle inside act, so
 * its state updates do not land after the test.
 */
async function renderSettled(ui: ReactElement) {
  const result = render(ui);
  await act(async () => {});
  return result;
}

describe('LogSearchBar Component (Items #6, #22, #31)', () => {
  it('names the severity and time selects by their visible labels', async () => {
    await renderSettled(
      <LogSearchBar
        filters={{}}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        availableSources={[]}
        availableApps={[]}
      />
    );

    expect(screen.getByRole('button', { name: 'Severity: All Severities' })).toHaveAttribute('aria-haspopup', 'listbox');
    expect(screen.getByRole('button', { name: 'Time: All Time' })).toHaveAttribute('aria-haspopup', 'listbox');
  });

  it('hides the Reset button when no filters are active', async () => {
    const filters: LogFilterParams = {};
    await renderSettled(
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

  it('shows the prominent Reset button with active count when query is present', async () => {
    const filters: LogFilterParams = { query: 'timeout' };
    await renderSettled(
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

  it('shows the prominent Reset button with aggregated count for multi-select hosts and apps', async () => {
    const filters: LogFilterParams = {
      query: 'error',
      severity_max: 3,
      sources: ['pve-node1', 'pve-node2'],
      apps: ['corosync', 'pvedaemon', 'kernel'],
    };
    await renderSettled(
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

  it('calls onReset and resets time preset when clicking Reset button', async () => {
    const handleReset = vi.fn();
    const filters: LogFilterParams = { query: 'panic' };
    await renderSettled(
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

  it('renders Host/IP and App/Container multi-select dropdowns on the filter row', async () => {
    const filters: LogFilterParams = {};
    await renderSettled(
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

  it('allows opening multi-select dropdown and selecting multiple options', async () => {
    const handleFilterChange = vi.fn();
    const filters: LogFilterParams = {};
    await renderSettled(
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

  it('does not display count indicator when no filters are active', async () => {
    const filters: LogFilterParams = {};
    await renderSettled(
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

  it('displays "1,000+ logs" next to Filter button when filter is active and totalCapped is true rather than literal 1,001', async () => {
    const filters: LogFilterParams = { query: 'timeout' };
    await renderSettled(
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

  it('displays exact log count next to Filter button when filter is active and totalCapped is false', async () => {
    const filters: LogFilterParams = { query: 'error' };
    await renderSettled(
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

describe('LogSearchBar search button and saved view errors', () => {
  it('calls onSearch without arguments when the Search now button is clicked', async () => {
    const onSearch = vi.fn();
    await renderSettled(
      <LogSearchBar filters={{ severity_max: 3 }} onFilterChange={vi.fn()} onSearch={onSearch} onReset={vi.fn()} />
    );
    fireEvent.click(screen.getByLabelText('Search now'));
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch.mock.calls[0]).toHaveLength(0);
  });

  it('shows an inline error when deleting a saved view fails', async () => {
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

  it('shows an inline error when pinning a saved view fails', async () => {
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

describe('LogSearchBar time presets', () => {
  const renderBar = async (filters: LogFilterParams, onFilterChange = vi.fn()) => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
    await renderSettled(<LogSearchBar filters={filters} onFilterChange={onFilterChange} onSearch={vi.fn()} onReset={vi.fn()} />);
    return onFilterChange;
  };

  it('stores a preset as a relative value instead of a fixed timestamp', async () => {
    const onFilterChange = await renderBar({ query: 'x' });
    chooseOption(toolbarSelect('Time'), 'Last 1h');
    expect(onFilterChange).toHaveBeenCalledWith({ query: 'x', from: '1h', to: undefined });
  });

  it('shows the preset that matches a relative from value (saved view or link)', async () => {
    await renderBar({ from: '24h' });
    expect(shownValue(toolbarSelect('Time'))).toBe('Last 24h');
  });

  it('shows Custom with the date pickers for an absolute from value', async () => {
    await renderBar({ from: '2026-10-13T10:00:00.000Z' });
    expect(shownValue(toolbarSelect('Time'))).toBe('Custom...');
    expect(screen.getAllByText('From:').length).toBeGreaterThan(0);
  });

  it('shows All Time when no time bounds are set', async () => {
    await renderBar({});
    expect(shownValue(toolbarSelect('Time'))).toBe('All Time');
  });

  it('turns a relative preset into an absolute start when switching to Custom', async () => {
    const onFilterChange = await renderBar({ from: '1h' });
    const before = Date.now();
    chooseOption(toolbarSelect('Time'), 'Custom...');
    const call = onFilterChange.mock.calls[0][0];
    const from = new Date(call.from).getTime();
    expect(from).toBeGreaterThanOrEqual(before - 3600_000 - 1000);
    expect(from).toBeLessThanOrEqual(Date.now() - 3600_000 + 1000);
    expect(shownValue(toolbarSelect('Time'))).toBe('Custom...');
  });
});

describe('LogSearchBar capped lists and Today preset', () => {
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
    chooseOption(toolbarSelect('Time'), 'Today');
    expect(onFilterChange).toHaveBeenCalledWith(expect.objectContaining({ from: 'since_midnight' }));
  });

  it('fills From with midnight on the server clock when switching from Today to Custom', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    // 03:00 UTC on 3 November is still 2 November in New York (the runner is in UTC)
    vi.setSystemTime(new Date('2026-11-03T03:00:00Z'));
    try {
      const onFilterChange = vi.fn();
      await renderSettled(
        <LogSearchBar {...baseProps} filters={{ from: 'since_midnight' }} onFilterChange={onFilterChange} timeZone="America/New_York" />
      );
      chooseOption(toolbarSelect('Time'), 'Custom...');
      expect(onFilterChange).toHaveBeenCalledWith(expect.objectContaining({ from: '2026-11-02T05:00:00.000Z' }));
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows and reads the custom range on the server clock, with the zone beside it', async () => {
    const onFilterChange = vi.fn();
    await renderSettled(
      <LogSearchBar
        {...baseProps}
        filters={{ from: '2026-07-01T12:00:00.000Z' }}
        onFilterChange={onFilterChange}
        timeZone="Europe/London"
      />
    );
    const from = screen.getAllByLabelText('From:')[0] as HTMLInputElement;
    expect(from.value).toBe('2026-07-01T13:00');
    expect(screen.getAllByText('Europe/London').length).toBeGreaterThan(0);
    fireEvent.change(from, { target: { value: '2026-07-01T09:15' } });
    expect(onFilterChange).toHaveBeenCalledWith(expect.objectContaining({ from: '2026-07-01T08:15:00.000Z' }));
  });

  it('shows Today for a saved view or link carrying since_midnight', async () => {
    await renderSettled(<LogSearchBar {...baseProps} filters={{ from: 'since_midnight' }} onFilterChange={vi.fn()} />);
    expect(shownValue(toolbarSelect('Time'))).toBe('Today');
  });
});

describe('LogSearchBar severity wording', () => {
  beforeEach(() => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
  });

  it('desktop select uses compact labels and a long title', async () => {
    await renderSettled(
      <LogSearchBar filters={{ severity_max: 4 }} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} />
    );
    const trigger = toolbarSelect('Severity');
    expect(shownValue(trigger)).toBe('Warn+ (0-4)');
    expect(trigger.title).toBe('Warning (4) and more severe');
    fireEvent.click(trigger);
    expect(screen.getByRole('option', { name: 'Debug+ (0-7)' })).toBeInTheDocument();
  });

  it('mobile drawer select uses the long "and more severe" labels', async () => {
    await renderSettled(
      <LogSearchBar filters={{}} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} />
    );
    fireEvent.click(screen.getByTitle('Open filters drawer'));
    fireEvent.click(panelSelect('Severity'));
    expect(screen.getByRole('option', { name: 'Warning (4) and more severe' })).toBeInTheDocument();
    expect(screen.queryByText(/Maximum Severity/)).toBeNull();
  });
});

describe('LogSearchBar search button, drawer and syntax help', () => {
  const renderBar = async (filters: LogFilterParams = {}, onSearch = vi.fn()) => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
    await renderSettled(<LogSearchBar filters={filters} onFilterChange={vi.fn()} onSearch={onSearch} onReset={vi.fn()} />);
    return onSearch;
  };

  it('uses a secondary Search now icon button instead of the blue Filter button', async () => {
    const onSearch = await renderBar();
    const button = screen.getByRole('button', { name: 'Search now' });
    expect(button).toHaveAttribute('title', 'Search now (Enter)');
    expect(button.className).not.toContain('bg-accent-700');
    expect(screen.queryByText('Filter')).toBeNull();
    fireEvent.click(button);
    expect(onSearch).toHaveBeenCalledTimes(1);
  });

  it('mobile drawer closes and searches with Done', async () => {
    const onSearch = await renderBar();
    fireEvent.click(screen.getByTitle('Open filters drawer'));
    expect(screen.queryByText('Apply Filters')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onSearch).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Done' })).toBeNull());
  });

  it('shows the new placeholder', async () => {
    await renderBar();
    expect(screen.getByPlaceholderText('Search logs, e.g. "disk full", error OR timeout, app:nginx')).toBeInTheDocument();
  });

  it('centres the search icon, the help button and the clear button on the input', async () => {
    await renderBar({ query: 'error' });
    const centred = ['top-1/2', '-translate-y-1/2'];
    expect(screen.getByRole('textbox', { name: 'Search logs' }).parentElement!.querySelector(':scope > svg')).toHaveClass(...centred);
    for (const name of ['Search syntax help', 'Clear search query']) {
      const button = screen.getByRole('button', { name });
      expect(button).toHaveClass(...centred, 'leading-none');
      expect(button).not.toHaveClass('top-1.5');
    }
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
    expect(help.className).toContain('right-7');
  });
});

describe('LogSearchBar capped-list note follows the server limit', () => {
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

describe('LogSearchBar phone layout', () => {
  const renderBar = async (filters: LogFilterParams = {}) => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
    await renderSettled(
      <LogSearchBar filters={filters} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} totalCount={110} />
    );
  };

  /** The smallest element holding both a and b. */
  const commonParent = (a: Element, b: Element): Element => {
    let node: Element | null = a.parentElement;
    while (node && !node.contains(b)) node = node.parentElement;
    return node!;
  };

  it('gives the search input a line shared only with the search button', async () => {
    await renderBar({ query: 'disk', severity_max: 3 });
    const input = screen.getByLabelText('Search logs');
    const inputLine = commonParent(input, screen.getByRole('button', { name: 'Search now' }));
    expect(inputLine).not.toContainElement(screen.getByTitle('Open filters drawer'));
    expect(inputLine).not.toContainElement(screen.getByTitle('Reset all active filters'));
    expect(inputLine).not.toContainElement(screen.getByTestId('search-bar-total-count'));
    // Stacked on phones, one row from the md breakpoint
    expect(inputLine.parentElement!.className).toContain('flex-col');
    expect(inputLine.parentElement!.className).toContain('md:flex-row');
  });

  it('shows the Reset label on phones too', async () => {
    await renderBar({ query: 'disk' });
    const reset = screen.getByTitle('Reset all active filters');
    expect(within(reset).getByText('Reset').className).not.toContain('sr-only');
  });

  it('places the syntax help across the input, not off the edge of a narrow screen', async () => {
    await renderBar({ query: 'disk' });
    fireEvent.click(screen.getByRole('button', { name: 'Search syntax help' }));
    const popover = screen.getByRole('dialog', { name: 'Search syntax' });
    // Its containing block is the input's wrapper, so it spans the input on phones
    expect(popover.className).toContain('inset-x-0');
    expect(popover.parentElement!.className).toBe('');
    expect(commonParent(popover, screen.getByLabelText('Search logs')).className).toContain('relative');
  });
});

describe('LogSearchBar phone toolbar slot', () => {
  const renderBar = async (mobileToolbar?: ReactNode) => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
    await renderSettled(
      <LogSearchBar
        filters={{ query: 'disk' }}
        onFilterChange={vi.fn()}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        totalCount={110}
        mobileToolbar={mobileToolbar}
      />
    );
  };

  it('shows the toolbar on the Filters line, with icon-only Filters and Reset buttons', async () => {
    await renderBar(<button type="button">Scroll</button>);
    const line = screen.getByTestId('search-bar-mobile-line');
    expect(line).toContainElement(screen.getByRole('button', { name: 'Scroll' }));
    expect(line).toContainElement(screen.getByTitle('Open filters drawer'));
    // Icons only: the words are still read, not shown
    expect(within(screen.getByTitle('Open filters drawer')).getByText('Filters').className).toBe('sr-only');
    const reset = screen.getByTitle('Reset all active filters');
    expect(within(reset).getByText('Reset').className).toContain('sr-only md:not-sr-only');
    // The count stays visible next to the buttons
    expect(within(line).getByTestId('search-bar-total-count')).toHaveTextContent('110 logs');
  });

  it('keeps the labelled buttons when no toolbar is passed', async () => {
    await renderBar();
    expect(within(screen.getByTitle('Open filters drawer')).getByText('Filters').className).not.toContain('sr-only');
    const reset = screen.getByTitle('Reset all active filters');
    expect(within(reset).getByText('Reset').className).not.toContain('sr-only');
  });
});

describe('LogSearchBar shows which filters are active', () => {
  beforeEach(() => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
  });

  const renderBar = async (filters: LogFilterParams, onFilterChange = vi.fn(), onReset = vi.fn()) => {
    await renderSettled(
      <LogSearchBar
        filters={filters}
        onFilterChange={onFilterChange}
        onSearch={vi.fn()}
        onReset={onReset}
        availableSources={['host-a']}
        availableApps={['nginx']}
      />
    );
    return { onFilterChange, onReset };
  };
  const activeOf = (label: string) => screen.getByLabelText(label).closest('[data-active]')?.getAttribute('data-active');
  const openPanel = () => fireEvent.click(screen.getByTitle('Open filters drawer'));

  it('shows nothing as active when no filter is set, and offers no clear buttons', async () => {
    await renderBar({});
    expect(activeOf('Search logs')).toBe('false');
    expect(activeOf('Severity:')).toBe('false');
    expect(activeOf('Time:')).toBe('false');
    expect(screen.queryByRole('button', { name: 'Clear severity filter' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Clear time filter' })).toBeNull();
    openPanel();
    expect(activeOf('Severity')).toBe('false');
    expect(activeOf('Time Range')).toBe('false');
  });

  it('marks the search box, Severity and Time active in the toolbar and in the phone panel', async () => {
    await renderBar({ query: 'timeout', severity_max: 3, from: '24h' });
    expect(activeOf('Search logs')).toBe('true');
    expect(activeOf('Severity:')).toBe('true');
    expect(activeOf('Time:')).toBe('true');
    // The highlighted controls and the Reset count agree
    expect(screen.getByTitle('Reset all active filters')).toHaveTextContent('3');
    openPanel();
    expect(activeOf('Severity')).toBe('true');
    expect(activeOf('Time Range')).toBe('true');
  });

  it('marks Time active for a custom range with only a To date', async () => {
    await renderBar({ to: '2026-09-04T08:00:00.000Z' });
    expect(activeOf('Time:')).toBe('true');
  });

  it('counts Custom without dates as active, as the Reset count does', async () => {
    await renderBar({});
    chooseOption(toolbarSelect('Time'), 'Custom...');
    expect(activeOf('Time:')).toBe('true');
    expect(screen.getByTitle('Reset all active filters')).toHaveTextContent('1');
  });

  it('clears only the severity filter', async () => {
    const { onFilterChange } = await renderBar({ query: 'x', severity_max: 3, from: '1h', sources: ['host-a'], source: 'host-a' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear severity filter' })[0]);
    expect(onFilterChange).toHaveBeenCalledWith({ query: 'x', severity_max: undefined, from: '1h', sources: ['host-a'], source: 'host-a' });
  });

  it('clears only the time filter', async () => {
    const { onFilterChange } = await renderBar({ query: 'x', severity_max: 3, from: '2026-09-04T08:00:00.000Z', to: '2026-09-04T09:00:00.000Z' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear time filter' })[0]);
    expect(onFilterChange).toHaveBeenCalledWith({ query: 'x', severity_max: 3, from: undefined, to: undefined });
  });

  it('offers the same clear buttons in the phone panel', async () => {
    const { onFilterChange } = await renderBar({ severity_max: 3, from: '1h' });
    openPanel();
    // The toolbar's controls stay in the page (hidden by CSS on phones); the panel's come after them
    const severityClears = screen.getAllByRole('button', { name: 'Clear severity filter' });
    const timeClears = screen.getAllByRole('button', { name: 'Clear time filter' });
    expect(severityClears).toHaveLength(2);
    expect(timeClears).toHaveLength(2);
    expect(screen.getByLabelText('Severity').closest('[data-active]')).toContainElement(severityClears[1]);
    fireEvent.click(severityClears[1]);
    expect(onFilterChange).toHaveBeenLastCalledWith({ severity_max: undefined, from: '1h' });
    fireEvent.click(timeClears[1]);
    expect(onFilterChange).toHaveBeenLastCalledWith({ severity_max: 3, from: undefined, to: undefined });
  });

  it('Reset clears every filter at once', async () => {
    const { onReset } = await renderBar({ query: 'x', severity_max: 3, from: '1h' });
    fireEvent.click(screen.getByTitle('Reset all active filters'));
    expect(onReset).toHaveBeenCalledTimes(1);
  });
});

describe('LogSearchBar filter controls name what they show', () => {
  beforeEach(() => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
  });

  const renderBar = async (filters: LogFilterParams, mobileToolbar?: ReactNode, onFilterChange = vi.fn()) => {
    await renderSettled(
      <LogSearchBar
        filters={filters}
        onFilterChange={onFilterChange}
        onSearch={vi.fn()}
        onReset={vi.fn()}
        availableSources={['host-a', 'host-b']}
        availableApps={['nginx']}
        mobileToolbar={mobileToolbar}
      />
    );
    return { onFilterChange };
  };

  it('puts the active filter count in the names of the Filters and Reset buttons', async () => {
    await renderBar({ query: 'x', severity_max: 3, from: '1h' });
    expect(screen.getByRole('button', { name: spacedName('Filters 3 active') })).toHaveTextContent('Filters 3');
    expect(screen.getByRole('button', { name: spacedName('Reset 3 active filters') })).toHaveTextContent('Reset 3');
  });

  it('keeps the count in the names when the phone toolbar shows icons only', async () => {
    await renderBar({ query: 'x' }, <button type="button">Scroll</button>);
    expect(screen.getByRole('button', { name: spacedName('Filters 1 active') })).toBeInTheDocument();
    const reset = screen.getByRole('button', { name: spacedName('Reset 1 active filters') });
    // Out of sight on phones but still read, so the name starts with the word shown from md up
    expect(within(reset).getByText('Reset').className).toContain('sr-only md:not-sr-only');
  });

  it('names the Filters button without a count when nothing is filtering', async () => {
    await renderBar({});
    expect(screen.getByRole('button', { name: 'Filters' })).toBeInTheDocument();
  });

  it('keeps the Host and App clear buttons out of the dropdown trigger', async () => {
    const { onFilterChange } = await renderBar({ sources: ['host-a', 'host-b'], source: 'host-a,host-b', apps: ['nginx'], app_name: 'nginx' });
    const hostClear = screen.getAllByRole('button', { name: 'Clear host / ip filter' })[0];
    const hostTrigger = screen.getByRole('button', { name: spacedName('Host / IP: host-a (+1) 2 selected') });
    expect(hostTrigger).not.toContainElement(hostClear);
    expect(hostTrigger.parentElement).toContainElement(hostClear);
    const appClear = screen.getAllByRole('button', { name: 'Clear app / container filter' })[0];
    expect(screen.getByRole('button', { name: 'App / Container: nginx' })).not.toContainElement(appClear);

    fireEvent.click(hostClear);
    expect(onFilterChange).toHaveBeenCalledWith(expect.objectContaining({ sources: [], source: undefined }));
    // Clearing does not open the list
    expect(hostTrigger).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('LogSearchBar Severity and Time dropdowns', () => {
  beforeEach(() => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
  });

  function Stateful({ initial = {} }: { initial?: LogFilterParams }) {
    const [filters, setFilters] = useState<LogFilterParams>(initial);
    return <LogSearchBar filters={filters} onFilterChange={setFilters} onSearch={vi.fn()} onReset={vi.fn()} />;
  }

  it('shows the From and To fields when Custom... is chosen in the toolbar', async () => {
    await renderSettled(<Stateful />);
    expect(screen.queryByText('From:')).toBeNull();
    chooseOption(toolbarSelect('Time'), 'Custom...');
    expect(screen.getAllByText('From:').length).toBeGreaterThan(0);
    expect(screen.getAllByText('To:').length).toBeGreaterThan(0);
  });

  it('gives the toolbar From and To boxes the height of the dropdowns, so the row does not grow', async () => {
    await renderSettled(<Stateful />);
    chooseOption(toolbarSelect('Time'), 'Custom...');
    const from = screen.getAllByLabelText('From:')[0].parentElement!;
    const to = screen.getAllByLabelText('To:')[0].parentElement!;
    expect(from.className).toContain('h-[26px]');
    expect(to.className).toContain('h-[26px]');
    // No box around them adding its own border and padding
    expect(from.parentElement!.className).not.toContain('border');
  });

  it('shows the From and To fields when Custom Range... is chosen in the phone panel', async () => {
    await renderSettled(<Stateful />);
    fireEvent.click(screen.getByTitle('Open filters drawer'));
    chooseOption(panelSelect('Time Range'), 'Custom Range...');
    expect(within(screen.getByRole('dialog', { name: 'Filter Logs' })).getByLabelText('From:')).toBeInTheDocument();
  });

  it('chooses a severity in the phone panel with the keyboard, and Esc closes only the list', async () => {
    await renderSettled(<Stateful />);
    fireEvent.click(screen.getByTitle('Open filters drawer'));
    const trigger = panelSelect('Severity');
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Filter Logs' })).toBeInTheDocument();
    fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.keyDown(document.activeElement!, { key: 'e' });
    fireEvent.keyDown(document.activeElement!, { key: 'e' });
    fireEvent.keyDown(document.activeElement!, { key: 'Enter' });
    // Emergency, then Error: the second e moves on to the next row starting with it
    expect(shownValue(panelSelect('Severity'))).toBe('Error (3) and more severe');
    expect(shownValue(toolbarSelect('Severity'))).toBe('Error+ (0-3)');
    expect(screen.getAllByRole('button', { name: 'Clear severity filter' })).toHaveLength(2);
  });
});
