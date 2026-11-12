// Host names: aliases, Docker hosts, and renames or removals while the Console is open.
import { screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import * as logsApi from '../api/logs.ts';
import * as aliasesApi from '../api/aliases.ts';
import * as dockerHostsApi from '../api/dockerHosts.ts';
import { LogEntry } from '../types.ts';
import { renderWithContext, setupConsoleMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

describe('Console host names and aliases', () => {
  setupConsoleMocks();

  it('deduplicates aliased hosts and never shows the raw IP when an alias is set', async () => {
    // 172.22.2.4 -> NPM and 192.168.1.50 -> homelab-host
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([
      { ip: '172.22.2.4', alias: 'NPM', notes: null, created_at: '2026-09-01T00:00:00Z' },
      { ip: '192.168.1.50', alias: 'homelab-host', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ]);
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

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

  it('updates displayed docker log rows when primary docker host alias changes and aliasVersion changes', async () => {
    const dockerLog: LogEntry = {
      id: 201,
      timestamp: '2026-09-03T14:30:15.123Z',
      received_at: '2026-09-03T14:30:15.150Z',
      source_ip: 'docker',
      source_alias: 'docker',
      app_name: 'vaultwarden',
      facility: 1,
      severity: 6,
      message: 'Vaultwarden ready',
      raw: 'Vaultwarden ready',
    };

    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValueOnce({
      logs: [dockerLog],
      total: 1,
      limit: 500,
      offset: 0,
    });

    let currentDockerEndpoints = [
      {
        id: 1,
        name: 'Primary Docker',
        url: 'unix:///var/run/docker.sock',
        source_alias: 'docker',
        enabled: true,
        tls_enabled: false,
        exclude_containers: '',
        created_at: '2026-09-01T00:00:00Z',
        updated_at: '2026-09-01T00:00:00Z',
        is_connected: true,
        active_containers: 5,
      },
    ];
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockImplementation(async () => currentDockerEndpoints);

    const { rerender } = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 0 });

    await waitFor(() => {
      expect(screen.getByText('Vaultwarden ready')).toBeInTheDocument();
    });

    const initialRows = document.querySelectorAll('.log-row');
    expect(within(initialRows[0] as HTMLElement).getByText('docker')).toBeInTheDocument();

    // Primary Docker endpoint alias updated to unraid-main
    currentDockerEndpoints = [
      {
        ...currentDockerEndpoints[0],
        source_alias: 'unraid-main',
      },
    ];

    // Increment aliasVersion
    act(() => {
      rerender(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 1 });
    });

    await waitFor(() => {
      const updatedRows = document.querySelectorAll('.log-row');
      expect(within(updatedRows[0] as HTMLElement).getByText('unraid-main')).toBeInTheDocument();
      expect(within(updatedRows[0] as HTMLElement).queryByText('docker')).toBeNull();
    });
  });

  it('keeps remote Docker and Syslog aliases strictly decoupled on the same host IP when either alias changes', async () => {
    const syslogLog: LogEntry = {
      id: 301,
      timestamp: '2026-09-03T14:30:15.123Z',
      received_at: '2026-09-03T14:30:15.150Z',
      source_ip: '192.168.1.50',
      source_alias: 'host-a',
      app_name: 'kernel',
      facility: 1,
      severity: 6,
      message: 'Array disk status ok',
      raw: 'Array disk status ok',
    };

    const dockerLog: LogEntry = {
      id: 302,
      timestamp: '2026-09-03T14:30:16.123Z',
      received_at: '2026-09-03T14:30:16.150Z',
      source_ip: '192.168.1.50',
      source_alias: 'host-a-docker',
      app_name: 'plex',
      facility: 1,
      severity: 6,
      message: 'Media scan complete',
      raw: 'Media scan complete',
    };

    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValueOnce({
      logs: [dockerLog, syslogLog],
      total: 2,
      limit: 500,
      offset: 0,
    });

    let currentAliases = [
      { ip: '192.168.1.50', alias: 'host-a', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ];
    vi.spyOn(aliasesApi, 'fetchAliases').mockImplementation(async () => currentAliases);

    let currentDockerEndpoints = [
      {
        id: 1,
        name: 'Primary Docker',
        url: 'unix:///var/run/docker.sock',
        source_alias: 'docker',
        enabled: true,
        tls_enabled: false,
        exclude_containers: '',
        created_at: '2026-09-01T00:00:00Z',
        updated_at: '2026-09-01T00:00:00Z',
        is_connected: true,
        active_containers: 5,
      },
      {
        id: 2,
        name: 'Remote Docker A',
        url: 'tcp://192.168.1.50:2375',
        source_alias: 'host-a-docker',
        enabled: true,
        tls_enabled: false,
        exclude_containers: '',
        created_at: '2026-09-01T00:00:00Z',
        updated_at: '2026-09-01T00:00:00Z',
        is_connected: true,
        active_containers: 3,
      },
    ];
    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockImplementation(async () => currentDockerEndpoints);

    const { rerender } = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 0 });

    await waitFor(() => {
      expect(screen.getByText('Media scan complete')).toBeInTheDocument();
      expect(screen.getByText('Array disk status ok')).toBeInTheDocument();
    });

    // 1. Update ONLY Docker alias to host-a-docker-new
    currentDockerEndpoints = [
      currentDockerEndpoints[0],
      {
        ...currentDockerEndpoints[1],
        source_alias: 'host-a-docker-new',
      },
    ];

    act(() => {
      rerender(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 1 });
    });

    await waitFor(() => {
      const rows = document.querySelectorAll('.log-row');
      // Docker log should have host-a-docker-new
      expect(within(rows[0] as HTMLElement).getByText('host-a-docker-new')).toBeInTheDocument();
      // Syslog log should still have host-a (NOT clobbered by docker alias)
      expect(within(rows[1] as HTMLElement).getByText('host-a')).toBeInTheDocument();
    });

    // 2. Update ONLY Syslog alias to host-a-new
    currentAliases = [
      { ip: '192.168.1.50', alias: 'host-a-new', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ];

    act(() => {
      rerender(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 2 });
    });

    await waitFor(() => {
      const rows = document.querySelectorAll('.log-row');
      // Docker log should still have host-a-docker-new (NOT clobbered by syslog alias)
      expect(within(rows[0] as HTMLElement).getByText('host-a-docker-new')).toBeInTheDocument();
      // Syslog log should now have host-a-new
      expect(within(rows[1] as HTMLElement).getByText('host-a-new')).toBeInTheDocument();
    });
  });

  it('does not corrupt already-loaded log aliases when mounted with aliasVersion > 0', async () => {
    const syslogLog: LogEntry = {
      id: 401,
      timestamp: '2026-09-03T14:30:15.123Z',
      received_at: '2026-09-03T14:30:15.150Z',
      source_ip: '192.168.1.50',
      source_alias: 'host-a',
      app_name: 'kernel',
      facility: 1,
      severity: 6,
      message: 'System booted',
      raw: 'System booted',
    };

    const dockerLog: LogEntry = {
      id: 402,
      timestamp: '2026-09-03T14:30:16.123Z',
      received_at: '2026-09-03T14:30:16.150Z',
      source_ip: '192.168.1.50',
      source_alias: 'host-a-docker-new',
      app_name: 'plex',
      facility: 1,
      severity: 6,
      message: 'Plex running',
      raw: 'Plex running',
    };

    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValueOnce({
      logs: [dockerLog, syslogLog],
      total: 2,
      limit: 500,
      offset: 0,
    });

    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([
      { ip: '192.168.1.50', alias: 'host-a', notes: null, created_at: '2026-09-01T00:00:00Z' },
    ]);

    vi.spyOn(dockerHostsApi, 'fetchDockerEndpoints').mockResolvedValue([
      {
        id: 1,
        name: 'Primary Docker',
        url: 'unix:///var/run/docker.sock',
        source_alias: 'docker',
        enabled: true,
        tls_enabled: false,
        exclude_containers: '',
        created_at: '2026-09-01T00:00:00Z',
        updated_at: '2026-09-01T00:00:00Z',
        is_connected: true,
        active_containers: 5,
      },
      {
        id: 2,
        name: 'Remote Docker A',
        url: 'tcp://192.168.1.50:2375',
        source_alias: 'host-a-docker-new',
        enabled: true,
        tls_enabled: false,
        exclude_containers: '',
        created_at: '2026-09-01T00:00:00Z',
        updated_at: '2026-09-01T00:00:00Z',
        is_connected: true,
        active_containers: 3,
      },
    ]);

    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />, { aliasVersion: 3 });

    await waitFor(() => {
      expect(screen.getByText('Plex running')).toBeInTheDocument();
      expect(screen.getByText('System booted')).toBeInTheDocument();
    });

    const rows = document.querySelectorAll('.log-row');
    expect(within(rows[0] as HTMLElement).getByText('host-a-docker-new')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText('host-a')).toBeInTheDocument();
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

    // The history page arrives after the alias list, as it usually does
    vi.spyOn(logsApi, 'fetchLogs').mockImplementationOnce(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      return { logs: [internalLog], total: 1, limit: 500, offset: 0 };
    });

    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('LogShed internal status check')).toBeInTheDocument();
    });

    // The displayed log row must reflect 'Local', NOT the unaliased 'logshed'
    const row = document.querySelector('.log-row') as HTMLElement;
    expect(within(row).getByText('Local')).toBeInTheDocument();
    expect(within(row).queryByText('logshed')).toBeNull();
  });

  it('shows host aliases on the first history page when it arrives before the alias list', async () => {
    const syslogLog: LogEntry = {
      id: 556,
      timestamp: '2026-09-03T12:00:00.000Z',
      received_at: '2026-09-03T12:00:00.010Z',
      source_ip: '192.168.1.20',
      source_alias: 'nas-raw-hostname',
      app_name: 'smartd',
      facility: 3,
      severity: 6,
      message: 'Disk check passed',
      raw: 'Disk check passed',
    };
    let releaseAliases: (list: Awaited<ReturnType<typeof aliasesApi.fetchAliases>>) => void = () => {};
    vi.spyOn(aliasesApi, 'fetchAliases').mockImplementationOnce(
      () => new Promise((resolve) => { releaseAliases = resolve; })
    );
    vi.spyOn(logsApi, 'fetchLogs').mockResolvedValueOnce({ logs: [syslogLog], total: 1, limit: 500, offset: 0 });

    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Disk check passed')).toBeInTheDocument();
    });
    const row = () => document.querySelector('.log-row') as HTMLElement;
    expect(within(row()).getByText('nas-raw-hostname')).toBeInTheDocument();

    await act(async () => {
      releaseAliases([{ ip: '192.168.1.20', alias: 'NAS', notes: null, created_at: '2026-09-01T00:00:00Z' }]);
    });

    await waitFor(() => {
      expect(within(row()).getByText('NAS')).toBeInTheDocument();
    });
    expect(within(row()).queryByText('nas-raw-hostname')).toBeNull();
  });
});
