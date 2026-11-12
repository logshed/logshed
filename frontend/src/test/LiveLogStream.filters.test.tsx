// Search, host and app filters, time presets, Quick Filters and saved views.
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import { QUICK_FILTER_RERANK_MS } from '../utils/quickFilters.ts';
import * as savedViewsApi from '../api/savedViews.ts';
import * as settingsApi from '../api/settings.ts';
import * as logsApi from '../api/logs.ts';
import * as aliasesApi from '../api/aliases.ts';
import * as dockerHostsApi from '../api/dockerHosts.ts';
import { LogEntry } from '../types.ts';
import { AliasContext } from '../context/AliasContext.tsx';
import { chooseOption, shownValue, toolbarSelect } from './selectDropdownHelpers.ts';
import { MockEventSource, virtualizerControl, sampleLogs, renderWithContext, setupConsoleMocks, setupStreamMocks, setupConsoleControlsMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

describe('Console search and filters', () => {
  setupConsoleMocks();

  it('maintains quick filter buttons even after a filter is applied', async () => {
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
    expect(screen.getByText('Waiting for new logs...')).toBeInTheDocument();

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

  it('debounces search-as-you-type so only the settled query is fetched from the backend', async () => {
    const fetchLogsSpy = vi.spyOn(logsApi, 'fetchLogs');
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });
    fetchLogsSpy.mockClear();

    const searchInput = screen.getByPlaceholderText(/Search logs/i);
    for (const partial of ['e', 'ex', 'exi', 'exis', 'exist', 'existi', 'existin', 'existing']) {
      fireEvent.change(searchInput, { target: { value: partial } });
    }

    await waitFor(() => {
      expect(fetchLogsSpy).toHaveBeenCalledWith(expect.objectContaining({ query: 'existing', offset: 0 }), expect.anything());
    });
    const queriedTerms = fetchLogsSpy.mock.calls.map(([params]) => params?.query);
    expect(queriedTerms).toEqual(['existing']);
  });

  it('submits the search immediately on Enter without a duplicate debounced fetch', async () => {
    const fetchLogsSpy = vi.spyOn(logsApi, 'fetchLogs');
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });
    fetchLogsSpy.mockClear();

    const searchInput = screen.getByPlaceholderText(/Search logs/i);
    vi.useFakeTimers();
    try {
      fireEvent.change(searchInput, { target: { value: 'timeout' } });
      fireEvent.keyDown(searchInput, { key: 'Enter', code: 'Enter' });

      expect(fetchLogsSpy).toHaveBeenCalledTimes(1);
      expect(fetchLogsSpy).toHaveBeenCalledWith(expect.objectContaining({ query: 'timeout', offset: 0 }), expect.anything());

      // Outlast the 300 ms debounce and confirm no second request was dispatched
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
    } finally {
      vi.useRealTimers();
    }
    expect(fetchLogsSpy.mock.calls.filter(([params]) => params?.offset === 0)).toHaveLength(1);
  });

  it('shows an updating indicator while a filter reload is in flight over existing results', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });
    expect(screen.queryByText('Updating results...')).toBeNull();

    let resolvePending: (value: logsApi.LogListResult) => void = () => {};
    vi.spyOn(logsApi, 'fetchLogs').mockImplementation(
      () => new Promise<logsApi.LogListResult>((resolve) => {
        resolvePending = resolve;
      }),
    );

    chooseOption(toolbarSelect('Severity'), 'Error+ (0-3)');

    await waitFor(() => {
      expect(screen.getByText('Updating results...')).toBeInTheDocument();
    });
    // Previous results remain visible while the reload is pending
    expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();

    await act(async () => {
      resolvePending({ logs: [sampleLogs[0]], total: 1, limit: 500, offset: 0 });
    });

    await waitFor(() => {
      expect(screen.queryByText('Updating results...')).toBeNull();
    });
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

    // Now slow filtered request resolves AFTER reset has already finished; give microtasks time to
    // run inside act, so anything the stale response still updates is applied before the checks
    await act(async () => {
      if (slowResolve) (slowResolve as () => void)();
      await new Promise((r) => setTimeout(r, 50));
    });

    // The live logs MUST NOT be overwritten by the stale slow filtered response!
    expect(screen.getByText('Container started cleanly')).toBeInTheDocument();
    expect(screen.getByText('Default deny rule matched WAN block')).toBeInTheDocument();
  });
});

describe('Console time presets and saved views', () => {
  const { renderStream } = setupStreamMocks();

  it('keeps a relative time from a link and shows the matching preset', async () => {
    window.history.replaceState(null, '', '/?time=1h');
    const fetchSpy = vi.spyOn(logsApi, 'fetchLogs');
    renderStream();
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith(expect.objectContaining({ from: '1h' }), expect.anything()));
    expect(shownValue(toolbarSelect('Time'))).toBe('Last 1h');
  });

  it('shows Custom for a digest link with an absolute time', async () => {
    window.history.replaceState(null, '', '/?time=2026-10-13T10%3A00%3A00.000Z&severity=3');
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    expect(shownValue(toolbarSelect('Time'))).toBe('Custom...');
  });

  it('applying a saved view fetches once and keeps its relative time', async () => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([
      { id: 9, name: 'Recent nginx', query_params: { query: 'nginx', from: '1h' }, is_pinned: false, created_at: '2026-10-01T00:00:00Z' },
    ] as any);
    const fetchSpy = vi.spyOn(logsApi, 'fetchLogs');
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    fireEvent.click(await screen.findByRole('button', { name: /Saved views menu/i }));
    const item = await screen.findByTestId('saved-view-item-9');
    fetchSpy.mockClear();
    vi.useFakeTimers();
    try {
      fireEvent.click(item);
      // Past the 300 ms search debounce: a second, debounced fetch would have fired by now
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
    } finally {
      vi.useRealTimers();
    }
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy).toHaveBeenCalledWith(expect.objectContaining({ query: 'nginx', from: '1h' }), expect.anything());
    expect(shownValue(toolbarSelect('Time'))).toBe('Last 1h');
  });
});

describe('LiveLogStream capped host list and Today preset', () => {
  const renderStream = () =>
    render(
      <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
        <LiveLogStream onDiagnoseAi={vi.fn()} />
      </AliasContext.Provider>
    );
  const facets = (truncated: boolean) => ({
    sources: ['host-a'],
    apps: ['nginx'],
    host_to_apps: { 'host-a': ['nginx'] },
    app_to_hosts: { nginx: ['host-a'] },
    truncated,
    limit: 500,
  });

  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
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
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({ logs: [...sampleLogs], total: sampleLogs.length, limit: 500, offset: 0 });
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
  });


  it('shows the capped note in the host list when the facets were truncated', async () => {
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(facets(true) as any);
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    await act(async () => {});
    fireEvent.click(screen.getByText('All Hosts'));
    expect(screen.getByText(/Showing the first 500 hosts/)).toBeInTheDocument();
  });

  it('shows no capped note when the facets were not truncated', async () => {
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(facets(false) as any);
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    await act(async () => {});
    fireEvent.click(screen.getByText('All Hosts'));
    expect(screen.queryByText(/Showing the first 500/)).toBeNull();
  });

  it('sends from=since_midnight when Today is chosen', async () => {
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(facets(false) as any);
    const fetchSpy = vi.spyOn(logsApi, 'fetchLogs');
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    chooseOption(toolbarSelect('Time'), 'Today');
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(expect.objectContaining({ from: 'since_midnight' }), expect.anything())
    );
    expect(shownValue(toolbarSelect('Time'))).toBe('Today');
  });
});

describe('Console filter controls', () => {
  const { renderStream, mockSettings, mockLogs, waitForRows, chipTexts } = setupConsoleControlsMocks();

  describe('Quick Filters', () => {
    const mk = (id: number, host: string, app: string): LogEntry => ({
      id,
      timestamp: `2026-09-03T14:${String(59 - (id % 60)).padStart(2, '0')}:00.000Z`,
      received_at: '2026-09-03T15:00:00.000Z',
      source_ip: host,
      source_alias: host,
      app_name: app,
      facility: 1,
      severity: 6,
      message: `msg ${id} from ${app}`,
      raw: `msg ${id}`,
    });

    // Volume: hosts zeta 3, alpha 1, mid 2; apps: zz 3, yy 2, the rest 1 or 0
    const volumeLogs: LogEntry[] = [
      mk(1, 'zeta', 'zz'),
      mk(2, 'zeta', 'zz'),
      mk(3, 'zeta', 'zz'),
      mk(4, 'mid', 'yy'),
      mk(5, 'mid', 'yy'),
      mk(6, 'alpha', 'bb'),
    ];
    const volumeFacets = {
      sources: ['alpha', 'mid', 'zeta', 'idle-host'],
      apps: ['aa', 'bb', 'cc', 'dd', 'ee', 'ff', 'gg', 'yy', 'zz'],
      host_to_apps: {},
      app_to_hosts: {},
    };

    beforeEach(() => {
      mockSettings({});
      mockLogs(volumeLogs);
      vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(volumeFacets as any);
    });

    it('ranks host and app chips by buffer volume (ties alphabetical), top 6 each, with group labels', async () => {
      renderStream();
      await screen.findByText('msg 1 from zz');
      // The chips are ranked in an effect after the rows render, which a slow runner can run later
      await waitFor(() => expect(chipTexts('quick-filter-app')).toEqual(['zz', 'yy', 'bb', 'aa', 'cc', 'dd']));
      expect(chipTexts('quick-filter-host')).toEqual(['zeta', 'mid', 'alpha', 'idle-host']);
      expect(screen.getByText('Hosts')).toBeInTheDocument();
      expect(screen.getByText('Apps')).toBeInTheDocument();
      // The labels come before their group
      const hostsLabel = screen.getByText('Hosts');
      const firstHost = screen.getAllByTestId('quick-filter-host')[0];
      expect(hostsLabel.compareDocumentPosition(firstHost) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      const appsLabel = screen.getByText('Apps');
      const firstApp = screen.getAllByTestId('quick-filter-app')[0];
      expect(appsLabel.compareDocumentPosition(firstApp) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    describe('host and app chips as a drill-down', () => {
      const mappedFacets = {
        ...volumeFacets,
        host_to_apps: { zeta: ['zz', 'aa'], mid: ['yy', 'cc'], alpha: ['bb'] },
        app_to_hosts: { zz: ['zeta'], aa: ['zeta'], yy: ['mid'], cc: ['mid'], bb: ['alpha'] },
      };
      const chip = (testId: string, name: string) =>
        screen.getAllByTestId(testId).find((el) => el.textContent === name) as HTMLButtonElement;
      const start = async () => {
        vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(mappedFacets as any);
        renderStream();
        await screen.findByText('msg 1 from zz');
        // Wait for the ranking, not just the chips: unranked, zz is not among the top 6
        await waitFor(() => expect(chipTexts('quick-filter-app')).toEqual(['zz', 'yy', 'bb', 'aa', 'cc', 'dd']));
        return { hostsBefore: chipTexts('quick-filter-host'), appsBefore: chipTexts('quick-filter-app') };
      };

      it('shows the selected host\'s apps, ranked by their volume there, and keeps the hosts in place', async () => {
        const { hostsBefore } = await start();
        // The filtered buffer then holds only mid's rows
        mockLogs(volumeLogs.filter((l) => l.source_alias === 'mid'));
        fireEvent.click(chip('quick-filter-host', 'mid'));
        await waitFor(() => expect(chipTexts('quick-filter-app')).toEqual(['yy', 'cc']));
        expect(chipTexts('quick-filter-host')).toEqual(hostsBefore);
        expect(screen.getAllByTestId('quick-filter-host').every((el) => !(el as HTMLButtonElement).disabled)).toBe(true);
      });

      it('keeps every chip in place when an app is selected first and disables the hosts without it', async () => {
        const { hostsBefore, appsBefore } = await start();
        mockLogs(volumeLogs.filter((l) => l.app_name === 'zz'));
        fireEvent.click(chip('quick-filter-app', 'zz'));
        await waitFor(() => expect(chip('quick-filter-host', 'mid')).toBeDisabled());
        expect(chipTexts('quick-filter-host')).toEqual(hostsBefore);
        expect(chipTexts('quick-filter-app')).toEqual(appsBefore);
        expect(chip('quick-filter-host', 'zeta')).toBeEnabled();
        expect(chip('quick-filter-host', 'alpha')).toHaveAttribute('title', 'No logs from alpha for the selected app');
        // A disabled chip does nothing
        fireEvent.click(chip('quick-filter-host', 'mid'));
        expect(chip('quick-filter-host', 'mid')).toBeDisabled();

        // Then a host: the apps follow it, and the selected app stays
        fireEvent.click(chip('quick-filter-host', 'zeta'));
        await waitFor(() => expect(chipTexts('quick-filter-app')).toEqual(['zz', 'aa']));
        expect(chipTexts('quick-filter-host')).toEqual(hostsBefore);

        // Clearing both brings every chip back as it was
        mockLogs(volumeLogs);
        fireEvent.click(chip('quick-filter-host', 'zeta'));
        fireEvent.click(chip('quick-filter-app', 'zz'));
        await waitFor(() => expect(chipTexts('quick-filter-app')).toEqual(appsBefore));
        expect(chipTexts('quick-filter-host')).toEqual(hostsBefore);
        expect(screen.getAllByTestId('quick-filter-host').every((el) => !(el as HTMLButtonElement).disabled)).toBe(true);
      });
    });

    it('keeps a selected chip visible when it falls outside the top 6', async () => {
      window.history.replaceState(null, '', '/?app=gg');
      renderStream();
      await screen.findByText('msg 1 from zz');
      await waitFor(() => expect(chipTexts('quick-filter-app')).toContain('gg'));
      const apps = chipTexts('quick-filter-app');
      expect(apps).toHaveLength(7);
      expect(apps[apps.length - 1]).toBe('gg');
    });

    it('keeps the chip order while live and re-ranks after 30 seconds', async () => {
      let now = 1_000_000;
      vi.spyOn(Date, 'now').mockImplementation(() => now);
      renderStream();
      await screen.findByText('msg 1 from zz');
      await waitFor(() => expect(chipTexts('quick-filter-host')).toEqual(['zeta', 'mid', 'alpha', 'idle-host']));

      // idle-host becomes the busiest host, but within 30 seconds the order holds
      const es = MockEventSource.instances[MockEventSource.instances.length - 1];
      act(() => {
        for (let i = 0; i < 5; i += 1) es.emit('log', mk(100 + i, 'idle-host', 'zz'));
      });
      await screen.findByText('msg 104 from zz');
      expect(chipTexts('quick-filter-host')).toEqual(['zeta', 'mid', 'alpha', 'idle-host']);

      now += QUICK_FILTER_RERANK_MS;
      act(() => es.emit('log', mk(110, 'idle-host', 'zz')));
      await screen.findByText('msg 110 from zz');
      await waitFor(() => expect(chipTexts('quick-filter-host')).toEqual(['idle-host', 'zeta', 'mid', 'alpha']));
    });

    it('uses taller chips on touch screens and keeps desktop density', async () => {
      renderStream();
      await screen.findByText('msg 1 from zz');
      const chip = (await screen.findAllByTestId('quick-filter-host'))[0];
      expect(chip.className).toContain('py-1');
      expect(chip.className).toContain('md:py-0.5');
    });
  });

  it('opens on an absolute range from time and to URL params in custom-time mode', async () => {
    mockSettings({});
    const from = '2026-10-13T10:00:00.000Z';
    const to = '2026-10-13T12:00:00.000Z';
    window.history.replaceState(null, '', `/?time=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    const fetchSpy = mockLogs([...sampleLogs]);
    renderStream();
    await waitForRows();
    expect(fetchSpy).toHaveBeenCalledWith(expect.objectContaining({ from, to }), expect.anything());
    expect(shownValue(toolbarSelect('Time'))).toBe('Custom...');
    const pickers = Array.from(document.querySelectorAll('input[type="datetime-local"]')) as HTMLInputElement[];
    expect(pickers.length).toBeGreaterThanOrEqual(2);
    expect(pickers.every((p) => p.value !== '')).toBe(true);
    expect(screen.getByText('Historical Range (Stream Paused)')).toBeInTheDocument();
    // The URL keeps both bounds
    const params = new URLSearchParams(window.location.search);
    expect(params.get('time')).toBe(from);
    expect(params.get('to')).toBe(to);
  });
});

describe('Pinned saved views on the Quick Filters bar', () => {
  const views = [
    { id: 1, name: 'Nginx errors', query_params: { query: 'nginx', severity_max: 3 }, is_pinned: true, created_at: '2026-10-01T00:00:00Z' },
    { id: 2, name: 'Router', query_params: { sources: ['opnsense-router'], source: 'opnsense-router' }, is_pinned: true, created_at: '2026-10-01T00:00:00Z' },
    { id: 3, name: 'Unpinned view', query_params: { query: 'postgres' }, is_pinned: false, created_at: '2026-10-01T00:00:00Z' },
  ];

  const renderStream = () =>
    render(
      <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
        <LiveLogStream onDiagnoseAi={vi.fn()} />
      </AliasContext.Provider>
    );

  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    MockEventSource.instances = [];
    virtualizerControl.edgeRows = null;
    virtualizerControl.headOnly = false;
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
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue(views.map((v) => ({ ...v })) as any);
  });

  it('shows pinned views first on the bar, before the Hosts group, and leaves unpinned views out', async () => {
    renderStream();
    await waitFor(() => expect(screen.getAllByTestId('quick-filter-view')).toHaveLength(2));
    const chips = screen.getAllByTestId('quick-filter-view');
    expect(chips.map((c) => c.textContent)).toEqual(['Nginx errors', 'Router']);
    await screen.findByText('Hosts');
    expect(chips[1].compareDocumentPosition(screen.getByText('Hosts')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Saved views are loaded once and shared with the search bar
    expect(savedViewsApi.fetchSavedViews).toHaveBeenCalledTimes(1);
  });

  it('applies a view on click and highlights its chip and the Views button', async () => {
    renderStream();
    const chip = (await screen.findAllByTestId('quick-filter-view'))[0];
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('views-button-label')).toHaveTextContent('Views');
    const fetchSpy = vi.mocked(logsApi.fetchLogs);
    fetchSpy.mockClear();

    fireEvent.click(chip);

    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(expect.objectContaining({ query: 'nginx', severity_max: 3 }), expect.anything())
    );
    expect(screen.getAllByTestId('quick-filter-view')[0]).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByTestId('quick-filter-view')[1]).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('views-button-label')).toHaveTextContent('View: Nginx errors');
  });

  it('a second click on the active view clears its filters', async () => {
    renderStream();
    const chip = (await screen.findAllByTestId('quick-filter-view'))[0];
    fireEvent.click(chip);
    await waitFor(() => expect(screen.getAllByTestId('quick-filter-view')[0]).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.getAllByTestId('quick-filter-view')[0]).toHaveAttribute('title', 'Clear saved view Nginx errors');
    const fetchSpy = vi.mocked(logsApi.fetchLogs);
    fetchSpy.mockClear();

    fireEvent.click(screen.getAllByTestId('quick-filter-view')[0]);

    await waitFor(() => expect(screen.getAllByTestId('quick-filter-view')[0]).toHaveAttribute('aria-pressed', 'false'));
    expect(screen.getByTestId('views-button-label')).toHaveTextContent('Views');
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const params = fetchSpy.mock.calls[fetchSpy.mock.calls.length - 1][0];
    expect(params?.query).toBeFalsy();
    expect(params?.severity_max).toBeUndefined();
  });

  it('marks a view active when the filters match it in another form (host chip instead of the view)', async () => {
    renderStream();
    await screen.findAllByTestId('quick-filter-view');
    const routerHost = await waitFor(() => {
      const chip = screen.getAllByTestId('quick-filter-host').find((el) => el.textContent === 'opnsense-router');
      expect(chip).toBeTruthy();
      return chip as HTMLElement;
    });
    fireEvent.click(routerHost);
    await waitFor(() => expect(screen.getAllByTestId('quick-filter-view')[1]).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.getByTestId('views-button-label')).toHaveTextContent('View: Router');
  });

  it('a rename from the Views menu updates the pinned chip at once, without loading the list again', async () => {
    vi.spyOn(savedViewsApi, 'updateSavedView').mockImplementation(async (id, data) => ({
      ...views.find((v) => v.id === id)!,
      ...data,
    }) as any);
    renderStream();
    await screen.findAllByTestId('quick-filter-view');
    fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));
    fireEvent.click(screen.getByLabelText('Rename view Router'));
    const input = screen.getByLabelText(/View Name/);
    fireEvent.change(input, { target: { value: 'Firewall' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getAllByTestId('quick-filter-view').map((c) => c.textContent)).toEqual(['Firewall', 'Nginx errors']));
    expect(savedViewsApi.fetchSavedViews).toHaveBeenCalledTimes(1);
  });
});

describe('LiveLogStream passes the facets limit to the capped-list note', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.restoreAllMocks();
    MockEventSource.instances = [];
    virtualizerControl.edgeRows = null;
    virtualizerControl.headOnly = false;
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValue({ logs: [...sampleLogs], total: sampleLogs.length, limit: 500, offset: 0 });
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([]);
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({} as any);
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({
      sources: ['host-a'],
      apps: ['nginx'],
      host_to_apps: { 'host-a': ['nginx'] },
      app_to_hosts: { nginx: ['host-a'] },
      truncated: true,
      limit: 300,
    } as any);
  });

  it('shows the limit from the facets response', async () => {
    render(
      <AliasContext.Provider value={{ aliasVersion: 0, bumpAliasVersion: vi.fn() }}>
        <LiveLogStream onDiagnoseAi={vi.fn()} />
      </AliasContext.Provider>
    );
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    await act(async () => {});
    fireEvent.click(screen.getByText('All Hosts'));
    expect(screen.getByText(/Showing the first 300 hosts/)).toBeInTheDocument();
  });
});
