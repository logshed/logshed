import React from 'react';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LiveLogStream, formatLocalTimestamp, matchesSearchQuery } from '../components/logs/LiveLogStream.tsx';
import * as logsApi from '../api/logs.ts';
import * as aliasesApi from '../api/aliases.ts';
import { LogEntry } from '../types.ts';
import { AliasContext, AliasContextValue } from '../context/AliasContext.tsx';

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize?: () => number }) => {
    const size = estimateSize ? estimateSize() : 28;
    return {
      getTotalSize: () => count * size,
      getVirtualItems: () =>
        Array.from({ length: count }, (_, index) => ({
          key: index,
          index,
          start: index * size,
          size,
        })),
      scrollToIndex: vi.fn(),
    };
  },
}));

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  listeners: Record<string, ((event: any) => void)[]> = {};
  onerror: ((e: any) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    this.listeners = {};
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (event: any) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(cb);
  }

  removeEventListener(type: string, cb: (event: any) => void) {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== cb);
    }
  }

  close() {}

  emit(type: string, data: any) {
    if (this.listeners[type]) {
      this.listeners[type].forEach((cb) => cb({ data: JSON.stringify(data) }));
    }
  }
}

(globalThis as any).EventSource = MockEventSource;

const sampleLogs: LogEntry[] = [
  {
    id: 101,
    timestamp: '2026-09-03T14:30:15.123Z',
    received_at: '2026-09-03T14:30:15.150Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'nginx',
    facility: 1,
    severity: 3,
    message: 'Nginx upstream connection timeout',
    raw: '<11>1 2026-09-03T14:30:15.123Z homelab-host nginx - - - Nginx upstream connection timeout',
  },
  {
    id: 102,
    timestamp: '2026-09-03T14:30:10.456Z',
    received_at: '2026-09-03T14:30:10.460Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'postgres',
    facility: 1,
    severity: 4,
    message: 'Postgres slow query detected',
    raw: '<12>1 2026-09-03T14:30:10.456Z homelab-host postgres - - - Postgres slow query detected',
  },
  {
    id: 103,
    timestamp: '2026-09-03T14:30:05.789Z',
    received_at: '2026-09-03T14:30:05.800Z',
    source_ip: '192.168.1.1',
    source_alias: 'opnsense-router',
    app_name: 'filterlog',
    facility: 4,
    severity: 6,
    message: 'Default deny rule matched WAN block',
    raw: '<134>1 2026-09-03T14:30:05.789Z opnsense-router filterlog - - - Default deny rule matched WAN block',
  },
  {
    id: 104,
    timestamp: '2026-09-03T14:30:00.000Z',
    received_at: '2026-09-03T14:30:00.020Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'docker',
    facility: 1,
    severity: 6,
    message: 'Container started cleanly',
    raw: '<14>1 2026-09-03T14:30:00.000Z homelab-host docker - - - Container started cleanly',
  },
];

describe('formatLocalTimestamp Helper (Item #5)', () => {
  it("formats today's timestamps as HH:mm:ss.SSS", () => {
    const now = new Date();
    const utcString = now.toISOString();
    const formatted = formatLocalTimestamp(utcString);
    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');
    const millis = String(now.getMilliseconds()).padStart(3, '0');
    const expected = `${hours}:${minutes}:${seconds}.${millis}`;
    expect(formatted).toBe(expected);
  });

  it('formats historical timestamps (e.g. from yesterday or a past month) with date as DD MMM HH:mm:ss', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-01T12:00:00Z'));
    try {
      const past = new Date(2026, 8, 3, 14, 22, 1, 0); // 03 Sep 14:22:01 in local time
      const formatted = formatLocalTimestamp(past.toISOString());
      expect(formatted).toBe('03 Sep 14:22:01');
    } finally {
      vi.useRealTimers();
    }
  });

  it('handles empty and invalid timestamp strings gracefully', () => {
    expect(formatLocalTimestamp('')).toBe('');
    expect(formatLocalTimestamp('invalid-date')).toBe('invalid-date');
  });

  it('falls back to received_at when timestamp is in the future (>60s ahead of received_at)', () => {
    const now = new Date();
    const receivedAt = new Date(now.getTime() - 65000).toISOString();
    const futureTs = new Date(now.getTime() + 100000).toISOString();
    const formatted = formatLocalTimestamp(futureTs, receivedAt);
    const d = new Date(receivedAt);
    const hours = String(d.getHours()).padStart(2, '0');
    const minutes = String(d.getMinutes()).padStart(2, '0');
    const seconds = String(d.getSeconds()).padStart(2, '0');
    const millis = String(d.getMilliseconds()).padStart(3, '0');
    expect(formatted).toBe(`${hours}:${minutes}:${seconds}.${millis}`);
  });

  it('falls back to received_at with historical format if received_at is from a prior day', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-01T12:00:00Z'));
    try {
      const pastReceived = new Date(2026, 8, 3, 14, 22, 1, 0);
      const pastFuture = new Date(2026, 8, 3, 15, 22, 1, 0); // 1 hour ahead
      const formatted = formatLocalTimestamp(pastFuture.toISOString(), pastReceived.toISOString());
      expect(formatted).toBe('03 Sep 14:22:01');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('LiveLogStream Component', () => {
  const renderWithContext = (
    ui: React.ReactElement,
    contextValue: Partial<AliasContextValue> = {}
  ) => {
    const value: AliasContextValue = {
      aliasVersion: 0,
      bumpAliasVersion: vi.fn(),
      ...contextValue,
    };
    const result = render(
      <AliasContext.Provider value={value}>
        {ui}
      </AliasContext.Provider>
    );
    return {
      ...result,
      rerender: (newUi: React.ReactElement, newContextValue: Partial<AliasContextValue> = {}) =>
        result.rerender(
          <AliasContext.Provider value={{ ...value, ...newContextValue }}>
            {newUi}
          </AliasContext.Provider>
        ),
    };
  };

  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    MockEventSource.instances = [];
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as any;
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({
      logs: [...sampleLogs],
      total: sampleLogs.length,
      limit: 500,
      offset: 0,
    });
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({
      sources: ['homelab-host', 'opnsense-router'],
      apps: ['nginx', 'postgres', 'docker', 'filterlog'],
      host_to_apps: {
        'homelab-host': ['docker', 'nginx', 'postgres'],
        'opnsense-router': ['filterlog'],
      },
      app_to_hosts: {
        docker: ['homelab-host'],
        nginx: ['homelab-host'],
        postgres: ['homelab-host'],
        filterlog: ['opnsense-router'],
      },
    });
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
  });

  it('displays Screen Buffer label and clear screen buffer tooltip (Item #14)', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/Screen Buffer:/)).toBeInTheDocument();
    });

    const trashBtn = screen.getByTitle('Clear screen buffer (clears browser view only; does not delete logs from disk)');
    expect(trashBtn).toBeInTheDocument();
  });

  it('does not render the redundant Maximize2 row action button (Item #17)', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Ensure Maximize2 "Inspect Detail & Context" button is absent
    expect(screen.queryByTitle('Inspect Detail & Context')).toBeNull();
    // Verify "Explain with AI" is present
    expect(screen.getAllByTitle('Explain with AI').length).toBeGreaterThan(0);
  });

  it('formats timestamps and renders UTC + received_at tooltip (Item #5)', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Check tooltip on the timestamp cell
    const expectedTime = formatLocalTimestamp(sampleLogs[0].timestamp);
    const timestampEl = screen.getByText(expectedTime);
    expect(timestampEl).toBeInTheDocument();
    expect(timestampEl.getAttribute('title')).toContain(sampleLogs[0].timestamp);
    expect(timestampEl.getAttribute('title')).toContain(sampleLogs[0].received_at);
  });

  it('maintains top-to-bottom stream direction and prepends SSE logs to the top (Item #4)', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Verify initial order is DESC (newest log id 101 appears before id 104)
    const logRows = document.querySelectorAll('.log-row');
    expect(logRows.length).toBe(4);
    expect(logRows[0]).toHaveTextContent('Nginx upstream connection timeout');
    expect(logRows[3]).toHaveTextContent('Container started cleanly');

    // Simulate new SSE event arrival
    const newEntry: LogEntry = {
      id: 105,
      timestamp: '2026-09-03T14:35:00.000Z',
      received_at: '2026-09-03T14:35:00.010Z',
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'kernel',
      facility: 0,
      severity: 2,
      message: 'Hardware error corrected by ECC',
      raw: '<10>1 2026-09-03T14:35:00.000Z homelab-host kernel - - - Hardware error corrected by ECC',
    };

    expect(MockEventSource.instances.length).toBeGreaterThan(0);
    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('log', newEntry);
    });

    // Newest log should be prepended to the top (index 0)
    await waitFor(() => {
      const updatedRows = document.querySelectorAll('.log-row');
      expect(updatedRows[0]).toHaveTextContent('Hardware error corrected by ECC');
    });
  });

  it('handles checkbox clicking without bubbling to row detail modal (Item #15)', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const logRows = document.querySelectorAll('.log-row');
    const firstRow = logRows[0];
    const checkboxContainer = firstRow.firstElementChild as HTMLElement;

    // Click checkbox container
    fireEvent.click(checkboxContainer);

    // Should select log without opening LogDetailModal
    await waitFor(() => {
      expect(screen.getByText('1 log selected')).toBeInTheDocument();
    });
    expect(screen.queryByText(/Log Record #101/i)).toBeNull();

    // Clicking anywhere else on row (e.g. message cell) opens detail modal
    const messageCell = screen.getByText('Nginx upstream connection timeout');
    fireEvent.click(messageCell);

    await waitFor(() => {
      expect(screen.getByText(/Log Record #101/i)).toBeInTheDocument();
    });
  });

  it('performs Shift-Click range selection across multiple hosts without error', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const logRows = document.querySelectorAll('.log-row');
    const row0Checkbox = logRows[0].firstElementChild as HTMLElement; // id 101, host: homelab-host
    const row1Checkbox = logRows[1].firstElementChild as HTMLElement; // id 102, host: homelab-host

    // Click row 0 (index 0)
    fireEvent.click(row0Checkbox);
    expect(screen.getByText('1 log selected')).toBeInTheDocument();

    // Shift-click row 1 (index 1)
    fireEvent.click(row1Checkbox, { shiftKey: true });
    expect(screen.getByText('2 logs selected')).toBeInTheDocument();

    // Now shift-click row 3 (index 3) - row 2 is opnsense-router (cross-host selection)
    const row3Checkbox = logRows[3].firstElementChild as HTMLElement; // id 104, host: homelab-host
    fireEvent.click(row3Checkbox, { shiftKey: true });

    // Should select all 4 logs across both hosts without any errors
    await waitFor(() => {
      expect(screen.getByText('4 logs selected')).toBeInTheDocument();
      expect(screen.queryByText(/Range selection contained logs from multiple hosts/i)).toBeNull();
      expect(screen.getByText(/2 hosts/i)).toBeInTheDocument();
    });
  });

  it('supports individual multi-host selection and shows multi-host status in action bar', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const logRows = document.querySelectorAll('.log-row');
    const row0Checkbox = logRows[0].firstElementChild as HTMLElement; // id 101, host: homelab-host
    const row2Checkbox = logRows[2].firstElementChild as HTMLElement; // id 103, host: opnsense-router

    // Select row 0 (homelab-host)
    fireEvent.click(row0Checkbox);
    expect(screen.getByText('1 log selected')).toBeInTheDocument();
    expect(screen.getAllByText('homelab-host').length).toBeGreaterThan(0);

    // Select row 2 (opnsense-router)
    fireEvent.click(row2Checkbox);
    expect(screen.getByText('2 logs selected')).toBeInTheDocument();
    expect(screen.getByText(/2 hosts/i)).toBeInTheDocument();
    expect(screen.queryByText(/Cannot select across different hosts/i)).toBeNull();
  });

  it('selects all logs currently in browser buffer with Select All action (capped at 200) and clears with Deselect All', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Locate "Select All" button in the toolbar
    const selectAllBtn = screen.getByRole('button', { name: /^Select All$/i });
    expect(selectAllBtn).toBeInTheDocument();

    // Click "Select All"
    fireEvent.click(selectAllBtn);

    // All 4 logs in sampleLogs should be selected
    await waitFor(() => {
      expect(screen.getByText('4 logs selected')).toBeInTheDocument();
    });

    // Deselect All button should now be available in toolbar and floating action bar
    const deselectAllBtns = screen.getAllByRole('button', { name: /^Deselect All$/i });
    expect(deselectAllBtns.length).toBeGreaterThan(0);

    // Click Deselect All from the floating action bar or toolbar
    fireEvent.click(deselectAllBtns[0]);

    // Selection should be cleared
    await waitFor(() => {
      expect(screen.queryByText('4 logs selected')).toBeNull();
    });
  });

  it('toggles Select All and Deselect All via table header column checkbox', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Find table header toggle button (aria-label "Toggle select all in table")
    const headerToggle = screen.getByRole('button', { name: 'Toggle select all in table' });
    expect(headerToggle).toBeInTheDocument();

    // Click table header toggle to select all
    fireEvent.click(headerToggle);

    await waitFor(() => {
      expect(screen.getByText('4 logs selected')).toBeInTheDocument();
    });

    // Now button aria-label should be "Toggle deselect all in table"
    const headerDeselectToggle = screen.getByRole('button', { name: 'Toggle deselect all in table' });
    expect(headerDeselectToggle).toBeInTheDocument();

    // Click table header toggle to deselect all
    fireEvent.click(headerDeselectToggle);

    await waitFor(() => {
      expect(screen.queryByText('4 logs selected')).toBeNull();
    });
  });

  it('displays auto-scroll pause banner and jumps to top on click (Item #4)', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Pause auto-scroll by clicking the auto-scroll toggle button
    const autoScrollBtn = screen.getByText('Auto-Scroll ON');
    fireEvent.click(autoScrollBtn);

    expect(screen.getByText('Paused')).toBeInTheDocument();

    // Emit a new log event while paused
    const newEntry: LogEntry = {
      id: 106,
      timestamp: '2026-09-03T14:40:00.000Z',
      received_at: '2026-09-03T14:40:00.010Z',
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'test',
      facility: 1,
      severity: 6,
      message: 'New incoming background log',
      raw: '<14>1 2026-09-03T14:40:00.000Z homelab-host test - - - New incoming background log',
    };

    const es = MockEventSource.instances[0];
    act(() => {
      es.emit('log', newEntry);
    });

    // Floating banner should appear with "Auto-scroll paused (1 new log at top)" and "Click to jump to top"
    await waitFor(() => {
      expect(screen.getByText(/Auto-scroll paused \(1 new log at top\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Click to jump to top/i)).toBeInTheDocument();
    });

    // Click floating banner to jump to top and resume
    const jumpBtn = screen.getByText(/Click to jump to top/i);
    fireEvent.click(jumpBtn);

    await waitFor(() => {
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
      expect(screen.queryByText(/Auto-scroll paused/i)).toBeNull();
    });
  });

  it('anchors scroll position when incoming logs arrive while auto-scroll is paused so visible logs do not move down', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Locate the scroll container
    const scrollContainer = document.querySelector('.overflow-y-auto') as HTMLElement;
    expect(scrollContainer).toBeInTheDocument();

    // Simulate scrolling down 200px (user looking at older logs)
    let scrollTopValue = 200;
    Object.defineProperty(scrollContainer, 'scrollTop', {
      get: () => scrollTopValue,
      set: (val: number) => { scrollTopValue = val; },
      configurable: true,
    });
    fireEvent.scroll(scrollContainer, { target: { scrollTop: 200 } });

    // Auto-scroll is now paused
    expect(screen.getByText('Paused')).toBeInTheDocument();

    // Emit 3 new incoming SSE logs
    const es = MockEventSource.instances[0];
    const newLogs: LogEntry[] = Array.from({ length: 3 }, (_, i) => ({
      id: 500 + i,
      timestamp: `2026-09-03T16:00:0${i}.000Z`,
      received_at: `2026-09-03T16:00:0${i}.010Z`,
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'test',
      facility: 1,
      severity: 6,
      message: `Anchored incoming log #${i}`,
      raw: `raw`,
    }));

    act(() => {
      newLogs.forEach((l) => es.emit('log', l));
    });

    // Wait for the floating paused banner (3 new logs at top)
    await waitFor(() => {
      expect(screen.getByText(/Auto-scroll paused \(3 new logs at top\)/i)).toBeInTheDocument();
    });

    // Verify scrollTop was compensated by 3 * 28 = 84px to prevent content jumping (200 + 84 = 284px)
    expect(scrollContainer.scrollTop).toBe(284);
  });

  it('maintains quick filter buttons even after a filter is applied (Item #32)', async () => {
    // Initially returns sampleLogs with homelab-host and opnsense-router
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Quick filter pills for both homelab-host and opnsense-router exist
    expect(screen.getByRole('button', { name: 'homelab-host' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'opnsense-router' })).toBeInTheDocument();

    // Mock fetchLogs to return only homelab-host logs when filtered
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({
      logs: [sampleLogs[0]],
      total: 1,
      limit: 500,
      offset: 0,
    });

    // Click quick filter pill for homelab-host
    fireEvent.click(screen.getByRole('button', { name: 'homelab-host' }));

    // Verify opnsense-router quick filter pill DOES NOT disappear!
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'homelab-host' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'opnsense-router' })).toBeInTheDocument();
    });

    // The homelab-host pill should now be active (highlighted)
    const towerBtn = screen.getByRole('button', { name: 'homelab-host' });
    expect(towerBtn.className).toContain('text-accent-300');
  });

  it('scopes app/container dropdown options to only apps belonging to the chosen host', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // 1. Initially without host filter, open App/Container dropdown
    const appTrigger = screen.getByText('App / Container:').closest('[role="button"]') as HTMLElement;
    const appDropdown = appTrigger.parentElement as HTMLElement;
    fireEvent.click(appTrigger);

    // All apps from both homelab-host (nginx, postgres, docker) and opnsense-router (filterlog) are present in the dropdown
    expect(within(appDropdown).getByText('filterlog')).toBeInTheDocument();
    expect(within(appDropdown).getByText('nginx')).toBeInTheDocument();
    expect(within(appDropdown).getByText('postgres')).toBeInTheDocument();

    // Close app dropdown
    fireEvent.click(appTrigger);

    // 2. Select host 'homelab-host'
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });
    act(() => {
      fireEvent.click(within(hostDropdown).getByText('homelab-host'));
      fireEvent.click(hostTrigger); // close host dropdown
    });

    // 3. Open App/Container dropdown again
    act(() => {
      fireEvent.click(appTrigger);
    });

    // Should contain homelab-host apps, but NOT opnsense-router's filterlog
    await waitFor(() => {
      expect(within(appDropdown).getByText('nginx')).toBeInTheDocument();
      expect(within(appDropdown).getByText('postgres')).toBeInTheDocument();
      expect(within(appDropdown).queryByText('filterlog')).toBeNull();
    });
  });

  it('scopes host dropdown options when an app is selected first (bidirectional scoping)', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const appTrigger = screen.getByText('App / Container:').closest('[role="button"]') as HTMLElement;
    const appDropdown = appTrigger.parentElement as HTMLElement;
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;

    // 1. Select app 'filterlog' (which only belongs to opnsense-router)
    act(() => {
      fireEvent.click(appTrigger);
    });
    act(() => {
      fireEvent.click(within(appDropdown).getByText('filterlog'));
      fireEvent.click(appTrigger); // close app dropdown
    });

    // 2. Open Host dropdown
    act(() => {
      fireEvent.click(hostTrigger);
    });

    // Should contain opnsense-router, but NOT homelab-host
    await waitFor(() => {
      expect(within(hostDropdown).getByText('opnsense-router')).toBeInTheDocument();
      expect(within(hostDropdown).queryByText('homelab-host')).toBeNull();
    });
  });

  it('captures click away on backdrop so clicking outside closes dropdown without opening log drawer', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // 1. Open Host dropdown
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });

    // Dropdown is open and backdrop interceptor exists
    const backdrop = within(hostDropdown).getByTestId('dropdown-backdrop');
    expect(backdrop).toBeInTheDocument();

    // 2. Click the backdrop
    act(() => {
      fireEvent.click(backdrop);
    });

    // 3. Dropdown closes, and log modal drawer did NOT open
    expect(within(hostDropdown).queryByTestId('dropdown-backdrop')).toBeNull();
    expect(screen.queryByText(/Log Record #/i)).toBeNull();
  });

  it('preserves full database hosts and apps in dropdowns even after clearing the screen buffer', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Clear the screen buffer
    const clearBtn = screen.getByTitle(/Clear screen buffer/i);
    act(() => {
      fireEvent.click(clearBtn);
    });

    // Buffer is empty: "0 lines"
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText(/No logs in stream/i)).toBeInTheDocument();

    // Now open the Host/IP dropdown
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });

    // Dropdown still contains homelab-host and opnsense-router from full database facets!
    expect(within(hostDropdown).getByText('homelab-host')).toBeInTheDocument();
    expect(within(hostDropdown).getByText('opnsense-router')).toBeInTheDocument();
  });

  it('deduplicates aliased hosts and never shows the raw IP when an alias is set', async () => {
    // Provide knownAliases with 172.22.2.4 -> NPM and 192.168.1.50 -> homelab-host
    renderWithContext(
      <LiveLogStream
        onDiagnoseAi={vi.fn()}
        knownAliases={{
          '172.22.2.4': 'NPM',
          '192.168.1.50': 'homelab-host',
        }}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Open Host dropdown
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });

    // NPM and homelab-host appear in the host dropdown
    expect(within(hostDropdown).getByText('homelab-host')).toBeInTheDocument();
    expect(within(hostDropdown).getByText('NPM')).toBeInTheDocument();

    // Raw IPs that are aliased (172.22.2.4 and 192.168.1.50) MUST NOT appear in the dropdown!
    expect(within(hostDropdown).queryByText('172.22.2.4')).toBeNull();
    expect(within(hostDropdown).queryByText('192.168.1.50')).toBeNull();
  });

  it('automatically fetches older logs with offset pagination on infinite scroll (Item #33)', async () => {
    // Generate 500 initial logs
    const batch1: LogEntry[] = Array.from({ length: 500 }, (_, i) => ({
      id: 1000 - i,
      timestamp: new Date(Date.now() - i * 1000).toISOString(),
      received_at: new Date(Date.now() - i * 1000).toISOString(),
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'nginx',
      facility: 1,
      severity: 3,
      message: `Batch 1 Log item #${1000 - i}`,
      raw: `Log ${1000 - i}`,
    }));

    const batch2: LogEntry[] = Array.from({ length: 250 }, (_, i) => ({
      id: 500 - i,
      timestamp: new Date(Date.now() - (500 + i) * 1000).toISOString(),
      received_at: new Date(Date.now() - (500 + i) * 1000).toISOString(),
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'nginx',
      facility: 1,
      severity: 3,
      message: `Batch 2 Log item #${500 - i}`,
      raw: `Log ${500 - i}`,
    }));

    const fetchLogsSpy = vi.spyOn(logsApi, 'fetchLogs').mockImplementation(async (params: any) => {
      if (!params.offset || params.offset === 0) {
        return { logs: batch1, total: 750, limit: 500, offset: 0 };
      }
      return { logs: batch2, total: 750, limit: 500, offset: 500 };
    });

    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    // Wait for initial batch of 500 logs to load
    await waitFor(() => {
      expect(screen.getByText('Batch 1 Log item #1000')).toBeInTheDocument();
    });

    // Virtualizer and scroll observer trigger loadMoreLogs for batch 2
    await waitFor(() => {
      expect(screen.getByText('Batch 2 Log item #500')).toBeInTheDocument();
    });

    // Total of 750 logs loaded, and end-of-history banner appears
    await waitFor(() => {
      expect(screen.getByText(/Reached beginning of log history \(750 logs loaded\)/i)).toBeInTheDocument();
    });

    // Verify fetchLogs was called with offset 0 and then offset 500
    expect(fetchLogsSpy).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, limit: 500 }));
    expect(fetchLogsSpy).toHaveBeenCalledWith(expect.objectContaining({ offset: 500, limit: 500 }));
  });

  it('pauses live SSE stream and displays indicator when a historical time ceiling (To) is set', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Initially with no 'to' filter, Auto-Scroll ON button is shown
    expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
    expect(screen.queryByText(/Historical Range \(Stream Paused\)/i)).toBeNull();

    // Select Custom time preset to reveal From and To inputs
    const timeSelect = screen.getByDisplayValue('All Time');
    fireEvent.change(timeSelect, { target: { value: 'custom' } });

    // Enter a past date into the "To:" input
    const datetimeInputs = document.querySelectorAll('input[type="datetime-local"]');
    const toInput = datetimeInputs[1] as HTMLInputElement;
    fireEvent.change(toInput, { target: { value: '2026-09-01T12:00' } });

    // The toolbar now displays the Historical Range (Stream Paused) badge instead of Auto-Scroll
    await waitFor(() => {
      expect(screen.getByText(/Historical Range \(Stream Paused\)/i)).toBeInTheDocument();
      expect(screen.queryByText('Auto-Scroll ON')).toBeNull();
    });
  });

  it('filters incoming SSE logs client-side when an active search query is applied', async () => {
    // Start with search filter applied
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({
      logs: [sampleLogs[0]], // only the nginx timeout log initially
      total: 1,
      limit: 500,
      offset: 0,
    });

    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Enter a search query in the search bar
    const searchInput = screen.getByPlaceholderText(/Full-text search/i);
    fireEvent.change(searchInput, { target: { value: 'timeout' } });
    fireEvent.keyDown(searchInput, { key: 'Enter', code: 'Enter' });

    await waitFor(() => {
      expect(searchInput).toHaveValue('timeout');
    });

    expect(MockEventSource.instances.length).toBeGreaterThan(0);
    const es = MockEventSource.instances[0];

    // 1. Emit an unrelated SSE log event (does not match 'timeout')
    const unrelatedLog: LogEntry = {
      id: 201,
      timestamp: '2026-09-03T14:50:00.000Z',
      received_at: '2026-09-03T14:50:00.010Z',
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'cron',
      facility: 9,
      severity: 6,
      message: 'Clean routine backup completed',
      raw: '<14>1 2026-09-03T14:50:00.000Z homelab-host cron - - - Clean routine backup completed',
    };

    act(() => {
      es.emit('log', unrelatedLog);
    });

    // 2. Emit a matching SSE log event (matches 'timeout')
    const matchingLog: LogEntry = {
      id: 202,
      timestamp: '2026-09-03T14:50:05.000Z',
      received_at: '2026-09-03T14:50:05.010Z',
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'nginx',
      facility: 1,
      severity: 3,
      message: 'Gateway timeout 504 on backend upstream',
      raw: '<11>1 2026-09-03T14:50:05.000Z homelab-host nginx - - - Gateway timeout 504 on backend upstream',
    };

    act(() => {
      es.emit('log', matchingLog);
    });

    // Matching log appears in stream
    await waitFor(() => {
      expect(screen.getByText('Gateway timeout 504 on backend upstream')).toBeInTheDocument();
    });

    // Unrelated non-matching log was dropped and NEVER appears in the DOM
    expect(screen.queryByText('Clean routine backup completed')).toBeNull();
  });

  it('batches rapid SSE event bursts into state updates and accumulates facets incrementally', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const es = MockEventSource.instances[0];

    // Emit 20 rapid SSE logs in a tight loop
    const rapidLogs: LogEntry[] = Array.from({ length: 20 }, (_, i) => ({
      id: 300 + i,
      timestamp: `2026-09-03T15:00:${String(i).padStart(2, '0')}.000Z`,
      received_at: `2026-09-03T15:00:${String(i).padStart(2, '0')}.010Z`,
      source_ip: '192.168.1.75',
      source_alias: 'storage-node',
      app_name: i === 19 ? 'zfs-scrub' : 'samba',
      facility: 1,
      severity: 6,
      message: `Rapid throughput event #${i}`,
      raw: `<14>1 2026-09-03T15:00:00.000Z storage-node samba - - - Rapid throughput event #${i}`,
    }));

    act(() => {
      rapidLogs.forEach((log) => es.emit('log', log));
    });

    // Verify newest log (index 19) is rendered at the top
    await waitFor(() => {
      expect(screen.getByText('Rapid throughput event #19')).toBeInTheDocument();
    });

    // Verify all 20 events are present in the list (total 4 original + 20 = 24 rows)
    const rows = document.querySelectorAll('.log-row');
    expect(rows.length).toBe(24);
    expect(rows[0]).toHaveTextContent('Rapid throughput event #19');
    expect(rows[19]).toHaveTextContent('Rapid throughput event #0');

    // Verify newly introduced source and app were incrementally accumulated
    expect(screen.getByRole('button', { name: 'storage-node' })).toBeInTheDocument();
  });

  it('resets stream back to live view when Reset button is clicked and restores all logs', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
      expect(screen.getByText('Container started cleanly')).toBeInTheDocument();
    });

    const filteredLog = sampleLogs[0]; // nginx
    vi.spyOn(logsApi, 'fetchLogs').mockImplementation(async (params) => {
      if (params?.sources && params.sources.includes('homelab-host')) {
        return {
          logs: [filteredLog],
          total: 1,
          limit: 500,
          offset: 0,
        };
      }
      return {
        logs: [...sampleLogs],
        total: sampleLogs.length,
        limit: 500,
        offset: 0,
      };
    });

    // Click quick filter for homelab-host
    fireEvent.click(screen.getByRole('button', { name: 'homelab-host' }));

    // Reset button should appear
    await waitFor(() => {
      expect(screen.getByTitle('Reset all active filters')).toBeInTheDocument();
    });

    // Click Reset button
    const resetBtn = screen.getByTitle('Reset all active filters');
    fireEvent.click(resetBtn);

    // Verify the stream refreshes and displays all logs again
    await waitFor(() => {
      expect(screen.getByText('Container started cleanly')).toBeInTheDocument();
      expect(screen.getByText('Default deny rule matched WAN block')).toBeInTheDocument();
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
    });

    // Reset button should now be gone
    expect(screen.queryByTitle('Reset all active filters')).toBeNull();
  });

  it('prevents race conditions where a slow filtered fetch resolves after Reset is clicked', async () => {
    let slowResolve: ((val: any) => void) | null = null;
    let fastResolve: ((val: any) => void) | null = null;

    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    vi.spyOn(logsApi, 'fetchLogs').mockImplementation(async (params) => {
      if (params?.sources && params.sources.includes('homelab-host')) {
        // Delayed filtered request
        return new Promise((resolve) => {
          slowResolve = () =>
            resolve({
              logs: [sampleLogs[0]],
              total: 1,
              limit: 500,
              offset: 0,
            });
        });
      }
      // Live / reset request
      return new Promise((resolve) => {
        fastResolve = () =>
          resolve({
            logs: [...sampleLogs],
            total: sampleLogs.length,
            limit: 500,
            offset: 0,
          });
      });
    });

    // Apply filter
    fireEvent.click(screen.getByRole('button', { name: 'homelab-host' }));

    // Before the filtered request finishes, user clicks Reset
    await waitFor(() => {
      expect(screen.getByTitle('Reset all active filters')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTitle('Reset all active filters'));

    // Now fast reset resolves first
    if (fastResolve) (fastResolve as () => void)();

    await waitFor(() => {
      expect(screen.getByText('Container started cleanly')).toBeInTheDocument();
    });

    // Now slow filtered request resolves AFTER reset has already finished
    if (slowResolve) (slowResolve as () => void)();

    // Give microtasks time to run
    await new Promise((r) => setTimeout(r, 50));

    // The live logs MUST NOT be overwritten by the stale slow filtered response!
    expect(screen.getByText('Container started cleanly')).toBeInTheDocument();
    expect(screen.getByText('Default deny rule matched WAN block')).toBeInTheDocument();
  });

  it('resets selectedLogIds when clearSelectionSignal is incremented', async () => {
    const { rerender } = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} clearSelectionSignal={0} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Select all logs
    const selectAllBtn = screen.getByRole('button', { name: /^select all$/i });
    fireEvent.click(selectAllBtn);

    await waitFor(() => {
      expect(screen.getByText(/selected/i)).toBeInTheDocument();
    });

    // Re-render with incremented clearSelectionSignal
    rerender(<LiveLogStream onDiagnoseAi={vi.fn()} clearSelectionSignal={1} />);

    await waitFor(() => {
      expect(screen.queryByText(/selected/i)).not.toBeInTheDocument();
    });
  });

  it('renders mobile card with app_name (host_name) on top line and timestamp at bottom', async () => {
    // Mock matchMedia to simulate mobile viewport
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query.includes('767px'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    expect(screen.getAllByText('(homelab-host)').length).toBeGreaterThan(0);
  });

  it('re-fetches aliases and updates displayed log rows when aliasVersion changes', async () => {
    let currentAliases = [
      { ip: '192.168.1.50', alias: 'homelab-host', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ];
    vi.spyOn(aliasesApi, 'fetchAliases').mockImplementation(async () => currentAliases);

    const { rerender } = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 0 });

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const initialRows = document.querySelectorAll('.log-row');
    expect(within(initialRows[0] as HTMLElement).getByText('homelab-host')).toBeInTheDocument();

    // Alias updated to nas-primary
    currentAliases = [
      { ip: '192.168.1.50', alias: 'nas-primary', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ];

    // Increment aliasVersion
    rerender(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 1 });

    await waitFor(() => {
      const updatedRows = document.querySelectorAll('.log-row');
      expect(within(updatedRows[0] as HTMLElement).getByText('nas-primary')).toBeInTheDocument();
      expect(within(updatedRows[0] as HTMLElement).queryByText('homelab-host')).toBeNull();
    });
  });

  it('reverts displayed log rows to raw IP when an alias is deleted and aliasVersion changes', async () => {
    let currentAliases = [
      { ip: '192.168.1.50', alias: 'homelab-host', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ];
    vi.spyOn(aliasesApi, 'fetchAliases').mockImplementation(async () => currentAliases);

    const { rerender } = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 0 });

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    const initialRows = document.querySelectorAll('.log-row');
    expect(within(initialRows[0] as HTMLElement).getByText('homelab-host')).toBeInTheDocument();

    // Alias deleted: empty list returned
    currentAliases = [];

    // Increment aliasVersion
    rerender(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 1 });

    await waitFor(() => {
      const updatedRows = document.querySelectorAll('.log-row');
      expect(within(updatedRows[0] as HTMLElement).getByText('192.168.1.50')).toBeInTheDocument();
      expect(within(updatedRows[0] as HTMLElement).queryByText('homelab-host')).toBeNull();
    });
  });

  it('updates quick filters and host dropdown options without ghost alias when aliasVersion changes', async () => {
    let currentAliases = [
      { ip: '192.168.1.50', alias: 'homelab-host', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ];
    vi.spyOn(aliasesApi, 'fetchAliases').mockImplementation(async () => currentAliases);

    const { rerender } = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 0 });

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Check quick filter button initially shows homelab-host
    expect(screen.getByRole('button', { name: /^homelab-host$/i })).toBeInTheDocument();

    // Alias updated to nas-primary
    currentAliases = [
      { ip: '192.168.1.50', alias: 'nas-primary', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ];

    // Increment aliasVersion
    act(() => {
      rerender(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 1 });
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^nas-primary$/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^homelab-host$/i })).toBeNull();
    });

    // Open Host dropdown to verify dropdown options
    const hostTrigger = screen.getByText('Host / IP:').closest('[role="button"]') as HTMLElement;
    const hostDropdown = hostTrigger.parentElement as HTMLElement;
    act(() => {
      fireEvent.click(hostTrigger);
    });

    expect(within(hostDropdown).getByText('nas-primary')).toBeInTheDocument();
    expect(within(hostDropdown).queryByText('homelab-host')).toBeNull();
    expect(within(hostDropdown).queryByText('192.168.1.50')).toBeNull();
  });

  it('resolves internal logshed logs to active alias in displayed log rows', async () => {
    const internalLog: LogEntry = {
      id: 555,
      timestamp: '2026-09-03T12:00:00.000Z',
      received_at: '2026-09-03T12:00:00.010Z',
      source_ip: '127.0.0.1',
      source_alias: 'logshed',
      app_name: 'syslog',
      facility: 1,
      severity: 6,
      message: 'LogShed internal status check',
      raw: 'LogShed internal status check',
    };

    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([
      { ip: '127.0.0.1', alias: 'Local', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ]);

    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValueOnce({
      logs: [internalLog],
      total: 1,
      limit: 500,
      offset: 0,
    });

    renderWithContext(
      <LiveLogStream onDiagnoseAi={vi.fn()} knownAliases={{ '127.0.0.1': 'Local' }} />
    );

    await waitFor(() => {
      expect(screen.getByText('LogShed internal status check')).toBeInTheDocument();
    });

    // The displayed log row must reflect 'Local', NOT the unaliased 'logshed'
    const row = document.querySelector('.log-row') as HTMLElement;
    expect(within(row).getByText('Local')).toBeInTheDocument();
    expect(within(row).queryByText('logshed')).toBeNull();
  });
});

describe('matchesSearchQuery Helper Function', () => {
  const baseLog: LogEntry = {
    id: 999,
    timestamp: '2026-09-03T12:00:00.000Z',
    received_at: '2026-09-03T12:00:00.010Z',
    source_ip: '192.168.1.50',
    source_alias: 'homelab-host',
    app_name: 'nginx',
    facility: 1,
    severity: 3,
    message: 'Upstream connection timeout to microservice',
    raw: 'raw log',
  };

  it('matches all logs when query is empty or whitespace', () => {
    expect(matchesSearchQuery(baseLog, '')).toBe(true);
    expect(matchesSearchQuery(baseLog, '   ')).toBe(true);
    expect(matchesSearchQuery(baseLog, undefined)).toBe(true);
  });

  it('performs case-insensitive substring matching on message, app_name, source_alias, and source_ip', () => {
    expect(matchesSearchQuery(baseLog, 'timeout')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'TIMEOUT')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'NGINX')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'homelab')).toBe(true);
    expect(matchesSearchQuery(baseLog, '192.168.1.50')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'unrelated-keyword')).toBe(false);
  });

  it('matches multiple space-separated terms', () => {
    expect(matchesSearchQuery(baseLog, 'nginx timeout')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'homelab microservice')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'nginx missing_term')).toBe(false);
  });

  it('supports regex patterns', () => {
    expect(matchesSearchQuery(baseLog, '^Upstream.*timeout')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'microservice$')).toBe(true);
    expect(matchesSearchQuery(baseLog, '^microservice')).toBe(false);
  });

  it('supports column-specific queries like app_name: and source:', () => {
    expect(matchesSearchQuery(baseLog, 'app_name:nginx')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'app:nginx')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'app_name:postgres')).toBe(false);
    expect(matchesSearchQuery(baseLog, 'source:homelab-host')).toBe(true);
    expect(matchesSearchQuery(baseLog, 'source:router')).toBe(false);
    expect(matchesSearchQuery(baseLog, 'message:timeout')).toBe(true);
  });
});



