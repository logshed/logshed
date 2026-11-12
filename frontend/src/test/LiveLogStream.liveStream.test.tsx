// The live stream, its connection, history pages and alert-link focus.
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import { parseFocusLogIdFromUrl } from '../utils/filters.ts';
import * as savedViewsApi from '../api/savedViews.ts';
import * as settingsApi from '../api/settings.ts';
import * as logsApi from '../api/logs.ts';
import * as aliasesApi from '../api/aliases.ts';
import * as dockerHostsApi from '../api/dockerHosts.ts';
import * as authApi from '../api/auth.ts';
import { LogEntry } from '../types.ts';
import { AliasContext } from '../context/AliasContext.tsx';
import { chooseOption, toolbarSelect } from './selectDropdownHelpers.ts';
import { MockEventSource, virtualizerControl, sampleLogs, renderWithContext, setupConsoleMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

describe('Console live stream and history pages', () => {
  setupConsoleMocks();

  it('maintains top-to-bottom stream direction and prepends SSE logs to the top', async () => {
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

  it('displays auto-scroll pause banner and jumps to top on click', async () => {
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

    // Once scrolling has stopped, scrollTop is compensated by 3 * 28 = 84px to prevent content jumping (200 + 84 = 284px)
    await waitFor(() => expect(scrollContainer.scrollTop).toBe(284));
  });

  it('automatically fetches older logs with offset pagination on infinite scroll', async () => {
    // Render only a viewport's worth of rows at each end, as the real virtualizer does
    virtualizerControl.edgeRows = 20;
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
      expect(screen.getByText('Batch 2 Log item #251')).toBeInTheDocument();
    });

    // Total of 750 logs loaded, and end-of-history banner appears
    await waitFor(() => {
      expect(screen.getByText(/Reached the end of loaded history \(750 logs loaded\)/i)).toBeInTheDocument();
    });

    // Verify fetchLogs was called with offset 0 and then offset 500
    expect(fetchLogsSpy).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, limit: 500 }), expect.anything());
    expect(fetchLogsSpy).toHaveBeenCalledWith(expect.objectContaining({ offset: 500, limit: 500 }), expect.anything());
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
    chooseOption(toolbarSelect('Time'), 'Custom...');

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
    const searchInput = screen.getByPlaceholderText(/Search logs/i);
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

  it('displays "1,000+ logs" when total_capped is true instead of a literal 1,001', async () => {
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValueOnce({
      logs: [...sampleLogs],
      total: 1001,
      total_capped: true,
      limit: 500,
      offset: 0,
    });

    window.history.replaceState({}, '', '?q=timeout');

    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Check that capped representation appears and literal 1,001 does not
    const cappedIndicators = screen.getAllByText('1,000+ logs');
    expect(cappedIndicators.length).toBeGreaterThan(0);
    expect(screen.queryByText('1,001')).toBeNull();
    expect(screen.queryByText('1,001 logs')).toBeNull();

    window.history.replaceState({}, '', '/');
  });
});

describe('LiveLogStream loading, connection and selection', () => {
  const renderStream = () =>
    render(
      <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
        <LiveLogStream onDiagnoseAi={vi.fn()} />
      </AliasContext.Provider>
    );

  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
    MockEventSource.instances = [];
    virtualizerControl.edgeRows = null;
    virtualizerControl.headOnly = false;
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
      apps: ['nginx'],
      host_to_apps: {},
      app_to_hosts: {},
    } as any);
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
  });

  it('Filter button loads logs with the current filters', async () => {
    const fetchSpy = vi.spyOn(logsApi, 'fetchLogs');
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    chooseOption(toolbarSelect('Severity'), 'Error+ (0-3)');
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(expect.objectContaining({ severity_max: 3 }), expect.anything());
    });
    fetchSpy.mockClear();
    fireEvent.click(screen.getByLabelText('Search now'));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const arg = fetchSpy.mock.calls[0][0] as any;
    expect(arg.severity_max).toBe(3);
    expect(arg.limit).toBe(500);
  });

  it('shows the error message when loading logs fails', async () => {
    vi.spyOn(logsApi, 'fetchLogs').mockRejectedValue(new Error('fts5: syntax error near "("'));
    renderStream();
    expect(await screen.findByText('fts5: syntax error near "("')).toBeInTheDocument();
  });

  it('shows a disconnected pill and retries when the live stream closes', async () => {
    renderStream();
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0] as any;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      es.readyState = 2;
      act(() => {
        es.onerror?.(new Event('error'));
      });
      expect(screen.getByText('Live stream disconnected')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
      vi.spyOn(authApi, 'getAuthStatus').mockResolvedValue({ setup_required: false, authenticated: true });
      const before = MockEventSource.instances.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2100);
      });
      expect(MockEventSource.instances.length).toBeGreaterThan(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it('dispatches logshed:unauthorized when the stream closes and the session is gone', async () => {
    renderStream();
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    const es = MockEventSource.instances[0] as any;
    const handler = vi.fn();
    window.addEventListener('logshed:unauthorized', handler);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.spyOn(authApi, 'getAuthStatus').mockResolvedValue({ setup_required: false, authenticated: false });
      es.readyState = 2;
      act(() => {
        es.onerror?.(new Event('error'));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2100);
      });
      expect(handler).toHaveBeenCalled();
    } finally {
      window.removeEventListener('logshed:unauthorized', handler);
      vi.useRealTimers();
    }
  });

  it('resets the shift-click anchor after deleting a log from the detail modal', async () => {
    vi.spyOn(logsApi, 'deleteSingleLog').mockResolvedValue(undefined as any);
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    // Anchor the range at row index 2 (id 103)
    fireEvent.click(document.querySelectorAll('.log-row')[2].firstElementChild as HTMLElement);
    expect(screen.getByText('1 log selected')).toBeInTheDocument();
    // Open log 101 (row 0) and delete it from the detail modal
    fireEvent.click(screen.getByText('Nginx upstream connection timeout'));
    await screen.findByText(/Log Record #101/i);
    fireEvent.click(screen.getByTitle('Delete this log record permanently'));
    fireEvent.click(screen.getByText('Confirm Delete'));
    await waitFor(() => expect(document.querySelectorAll('.log-row').length).toBe(3));
    // Shift-click row 0 (id 102): with a stale anchor this would select a whole range of 3
    fireEvent.click(document.querySelectorAll('.log-row')[0].firstElementChild as HTMLElement, { shiftKey: true });
    expect(screen.getByText('2 logs selected')).toBeInTheDocument();
  });
});

describe('Focus on the log an alert link points at', () => {
  const range = 'time=2026-09-03T14:29:00.000Z&to=2026-09-03T14:31:00.000Z';

  const renderStream = () =>
    render(
      <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
        <LiveLogStream onDiagnoseAi={vi.fn()} />
      </AliasContext.Provider>
    );

  const focusedRows = () => Array.from(document.querySelectorAll('.log-row[data-focused="true"]'));

  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.instances = [];
    virtualizerControl.edgeRows = null;
    virtualizerControl.headOnly = false;
    virtualizerControl.scrollToIndex = vi.fn();
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({} as any);
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({ logs: [...sampleLogs], total: sampleLogs.length, limit: 500, offset: 0 });
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({
      sources: ['homelab-host', 'opnsense-router'],
      apps: ['nginx'],
      host_to_apps: {},
      app_to_hosts: {},
    } as any);
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([]);
  });

  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('reads only a positive whole number from the focus parameter', () => {
    for (const [query, expected] of [
      ['?focus=103', 103],
      ['?focus=0', null],
      ['?focus=-3', null],
      ['?focus=abc', null],
      ['?focus=12.5', null],
      ['', null],
    ] as const) {
      window.history.replaceState(null, '', `/${query}`);
      expect(parseFocusLogIdFromUrl()).toBe(expected);
    }
  });

  it('scrolls to and highlights the focused log, and does not send focus as a filter', async () => {
    window.history.replaceState(null, '', `/?${range}&focus=${sampleLogs[2].id}`);
    renderStream();

    // The row is highlighted when it renders; the scroll follows in an effect, which a slow runner can run later
    await waitFor(() => {
      expect(focusedRows()).toHaveLength(1);
      expect(virtualizerControl.scrollToIndex).toHaveBeenCalledWith(2, { align: 'center' });
    });
    expect(focusedRows()[0]).toHaveTextContent(sampleLogs[2].message);
    const params = vi.mocked(logsApi.fetchLogs).mock.calls[0][0] as Record<string, unknown>;
    expect(params).not.toHaveProperty('focus');
    // The link's range is kept as filters, with the focus parameter beside them
    expect(window.location.search).toContain(`focus=${sampleLogs[2].id}`);
  });

  it('loads older pages until the focused log is found', async () => {
    const newerPage: LogEntry[] = Array.from({ length: 500 }, (_, i) => ({
      ...sampleLogs[0],
      id: 5000 - i,
      message: `later line ${i}`,
    }));
    const trigger: LogEntry = { ...sampleLogs[1], id: 4321, message: 'the line that fired the alert' };
    vi.mocked(logsApi.fetchLogs).mockImplementation(async (params: any) =>
      params.offset === 0
        ? { logs: newerPage, total: 501, limit: 500, offset: 0 }
        : { logs: [trigger], total: 501, limit: 500, offset: 500 }
    );
    virtualizerControl.edgeRows = 3;
    window.history.replaceState(null, '', `/?${range}&focus=4321`);
    renderStream();

    await waitFor(() => {
      expect(focusedRows()).toHaveLength(1);
      expect(virtualizerControl.scrollToIndex).toHaveBeenCalledWith(500, { align: 'center' });
    });
    expect(focusedRows()[0]).toHaveTextContent('the line that fired the alert');
    expect(vi.mocked(logsApi.fetchLogs).mock.calls.map((c) => (c[0] as any).offset)).toEqual([0, 500]);
  });

  it('stops highlighting and drops the focus parameter when the filters change', async () => {
    window.history.replaceState(null, '', `/?${range}&focus=${sampleLogs[0].id}`);
    renderStream();
    await waitFor(() => expect(focusedRows()).toHaveLength(1));

    const hostChip = await waitFor(() => {
      const chip = screen.getAllByTestId('quick-filter-host').find((el) => el.textContent === 'homelab-host');
      expect(chip).toBeTruthy();
      return chip as HTMLElement;
    });
    fireEvent.click(hostChip);

    await waitFor(() => expect(focusedRows()).toHaveLength(0));
    expect(window.location.search).not.toContain('focus=');
  });
});
