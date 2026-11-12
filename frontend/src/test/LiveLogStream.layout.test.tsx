// Layout on desktop and phones, timestamps, and the empty-list states.
import { screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import { onboardingLoggerCommand } from '../components/logs/ConsoleEmptyState.tsx';
import { formatStreamTimestamp } from '../utils/formatters.ts';
import * as settingsApi from '../api/settings.ts';
import * as logsApi from '../api/logs.ts';
import * as clipboardModule from '../utils/clipboard.ts';
import { MockEventSource, sampleLogs, renderWithContext, setupConsoleMocks, setupStreamMocks, setupConsoleControlsMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

describe('Console layout and time display', () => {
  setupConsoleMocks();

  it('displays Screen Buffer label and clear screen buffer tooltip', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/Screen Buffer:/)).toBeInTheDocument();
    });

    const trashBtn = screen.getByTitle('Clear screen buffer (clears browser view only; does not delete logs from disk)');
    expect(trashBtn).toBeInTheDocument();
  });

  it('does not render the redundant Maximize2 row action button', async () => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ ai_enabled: true } as any);
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Ensure Maximize2 "Inspect Detail & Context" button is absent
    expect(screen.queryByTitle('Inspect Detail & Context')).toBeNull();
    // Verify "Explain with AI" is present (AI is on)
    await waitFor(() => expect(screen.getAllByTitle('Explain with AI').length).toBeGreaterThan(0));
  });

  it('formats timestamps and renders UTC + received_at tooltip', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });

    // Check tooltip on the timestamp cell
    const expectedTime = formatStreamTimestamp(sampleLogs[0].timestamp, sampleLogs[0].received_at);
    const timestampEl = screen.getByText(expectedTime);
    expect(timestampEl).toBeInTheDocument();
    expect(timestampEl.getAttribute('title')).toContain(sampleLogs[0].timestamp);
    expect(timestampEl.getAttribute('title')).toContain(sampleLogs[0].received_at);
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

  it('on a phone, labels the auto-scroll button "Scroll" and leaves out the clear buffer button', async () => {
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
    expect(screen.getByText('Scroll')).toBeInTheDocument();
    expect(screen.queryByText('Auto-Scroll ON')).toBeNull();
    expect(screen.getByTitle('Auto-scroll is on: click to pause')).toBeInTheDocument();
    expect(screen.queryByTitle(/Clear screen buffer/i)).toBeNull();
  });

  it('on a phone, puts the quick filters and stream controls on the Filters line, saving a row', async () => {
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
    const line = screen.getByTestId('search-bar-mobile-line');
    expect(line).toContainElement(screen.getByTitle('Open filters drawer'));
    expect(line).toContainElement(screen.getByTitle('Auto-scroll is on: click to pause'));
    expect(screen.getAllByTitle('Auto-scroll is on: click to pause')).toHaveLength(1);
    expect(screen.queryByText('Quick Filters:')).toBeNull();
  });

  it('on a desktop, keeps the quick filters in their own band under the search bar', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument();
    });
    expect(screen.getByText('Quick Filters:')).toBeInTheDocument();
    expect(screen.getByTestId('search-bar-mobile-line')).not.toContainElement(
      screen.getByTitle('Auto-scroll is on: click to pause')
    );
  });
});

describe('Console time zone', () => {
  const { renderStream } = setupStreamMocks();

  it('re-formats loaded rows when the server time zone arrives after the first page', async () => {
    let resolveSettings: (s: any) => void = () => {};
    vi.spyOn(settingsApi, 'fetchSettings').mockImplementation(
      () => new Promise((resolve) => { resolveSettings = resolve; })
    );
    renderStream();
    const log = sampleLogs[0];
    const browserText = formatStreamTimestamp(log.timestamp, log.received_at);
    await waitFor(() => expect(screen.getByText(browserText)).toBeInTheDocument());

    // UTC+14: differs from any browser zone the suite runs in
    const tz = 'Pacific/Kiritimati';
    await act(async () => {
      resolveSettings({ server_timezone: tz, server_tz_name: 'LINT' });
    });
    const serverText = formatStreamTimestamp(log.timestamp, log.received_at, tz);
    expect(serverText).not.toBe(browserText);
    await waitFor(() => expect(screen.getByText(serverText)).toBeInTheDocument());
  });
});

describe('Console empty states', () => {
  const { emptyFacets, renderStream, mockSettings, mockLogs, waitForRows } = setupConsoleControlsMocks();

  describe('empty states', () => {
    it('shows "No logs match the current filters." with a Clear filters button when filters match nothing', async () => {
      mockSettings({});
      window.history.replaceState(null, '', '/?q=nothing-matches');
      const fetchSpy = mockLogs([]);
      renderStream();
      expect(await screen.findByText('No logs match the current filters.')).toBeInTheDocument();
      expect(screen.queryByText('No logs yet')).toBeNull();

      fetchSpy.mockResolvedValue({ logs: [...sampleLogs], total: sampleLogs.length, limit: 500, offset: 0 });
      fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
      await waitForRows();
      expect(window.location.search).toBe('');
      expect(fetchSpy).toHaveBeenLastCalledWith(
        expect.not.objectContaining({ query: expect.anything() }),
        expect.anything()
      );
    });

    it('prefers the filters message even when the database has no logs', async () => {
      mockSettings({});
      vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(emptyFacets as any);
      window.history.replaceState(null, '', '/?severity=2');
      mockLogs([]);
      renderStream();
      expect(await screen.findByText('No logs match the current filters.')).toBeInTheDocument();
      expect(screen.queryByText('No logs yet')).toBeNull();
    });

    it('shows "Waiting for new logs..." when the buffer is empty but the database has logs', async () => {
      mockSettings({});
      mockLogs([]);
      renderStream();
      expect(await screen.findByText('Waiting for new logs...')).toBeInTheDocument();
      await act(async () => {});
      expect(screen.queryByText('No logs yet')).toBeNull();
    });

    it('shows "Waiting for new logs..." after Clear buffer', async () => {
      mockSettings({});
      vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(emptyFacets as any);
      renderStream();
      await waitForRows();
      fireEvent.click(screen.getByTitle(/Clear screen buffer/i));
      expect(screen.getByText('Waiting for new logs...')).toBeInTheDocument();
      expect(screen.queryByText('No logs yet')).toBeNull();
    });

    it('shows the listening port from settings in the first-run card', async () => {
      mockSettings({ syslog_port: 5140 });
      vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(emptyFacets as any);
      mockLogs([]);
      const copySpy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
      renderStream({});

      expect(
        await screen.findByText(
          'LogShed listens for syslog on port 5140 (UDP and TCP) inside the container. Point your devices at the host port you mapped to it.'
        )
      ).toBeInTheDocument();
      expect(screen.getByText('logger -n <logshed-host> -P 5140 "Hello from LogShed"')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /Copy/ }));
      await waitFor(() => expect(copySpy).toHaveBeenCalledWith('logger -n <logshed-host> -P 5140 "Hello from LogShed"'));
    });

    it('shows the first-run card on a new install, where the facets already list the Primary Docker host', async () => {
      mockSettings({});
      // Configured hosts are listed before they log; only apps come from stored rows
      vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({ ...emptyFacets, sources: ['docker'] } as any);
      mockLogs([]);
      renderStream({});

      expect(await screen.findByRole('heading', { name: 'No logs yet' })).toBeInTheDocument();
    });

    it('shows the first-run card when the database has no logs at all', async () => {
      mockSettings({});
      vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(emptyFacets as any);
      mockLogs([]);
      const copySpy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
      const onOpenSourcesSettings = vi.fn();
      renderStream({ onOpenSourcesSettings });

      expect(await screen.findByRole('heading', { name: 'No logs yet' })).toBeInTheDocument();
      expect(
        screen.getByText(
          'LogShed listens for syslog on port 1514 (UDP and TCP) inside the container by default. Point your devices at the host port you mapped to it.'
        )
      ).toBeInTheDocument();
      expect(screen.getByText(onboardingLoggerCommand())).toBeInTheDocument();
      expect(onboardingLoggerCommand()).toBe('logger -n <logshed-host> -P 1514 "Hello from LogShed"');
      expect(screen.getByText('See Sending Logs in the documentation for device guides.')).toBeInTheDocument();
      expect(screen.queryByText('Waiting for new logs...')).toBeNull();

      fireEvent.click(screen.getByRole('button', { name: /Copy/ }));
      await waitFor(() => expect(copySpy).toHaveBeenCalledWith(onboardingLoggerCommand()));
      expect(await screen.findByText('Copied')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Set up Docker hosts' }));
      expect(onOpenSourcesSettings).toHaveBeenCalledTimes(1);
    });

    it('replaces the first-run card with the stream once a live log arrives', async () => {
      mockSettings({});
      vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue(emptyFacets as any);
      mockLogs([]);
      renderStream();
      await screen.findByText('No logs yet');
      const es = MockEventSource.instances[MockEventSource.instances.length - 1];
      act(() => es.emit('log', { ...sampleLogs[0], id: 900, message: 'First ever log' }));
      await screen.findByText('First ever log');
      expect(screen.queryByText('No logs yet')).toBeNull();
    });

    it('decides the first-run card from an unfiltered history page while the facets are still loading', async () => {
      mockSettings({});
      vi.spyOn(logsApi, 'fetchLogFacets').mockImplementation(() => new Promise(() => {}));
      mockLogs([]);
      renderStream();
      // An unfiltered page with a total of 0: the database is empty, whatever the facets say later
      expect(await screen.findByRole('heading', { name: 'No logs yet' })).toBeInTheDocument();
    });

    it('does not show the first-run card while a filtered search finds nothing and the facets are loading', async () => {
      mockSettings({});
      vi.spyOn(logsApi, 'fetchLogFacets').mockImplementation(() => new Promise(() => {}));
      mockLogs([]);
      window.history.replaceState(null, '', '/?q=nothing-matches');
      try {
        renderStream();
        expect(await screen.findByText('No logs match the current filters.')).toBeInTheDocument();
        expect(screen.queryByText('No logs yet')).toBeNull();
      } finally {
        window.history.replaceState(null, '', '/');
      }
    });
  });
});
