import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LogDetailModal } from '../components/logs/LogDetailModal.tsx';
import { LogEntry } from '../types.ts';
import * as logsApi from '../api/logs.ts';

describe('LogDetailModal Component (Items #8, #9, #11)', () => {
  const sampleLog: LogEntry = {
    id: 100,
    timestamp: '2026-08-29T14:30:00.000Z',
    received_at: '2026-08-29T14:30:00.100Z',
    source_ip: '192.168.1.50',
    source_alias: 'proxmox-01',
    app_name: 'pveproxy',
    facility: 1,
    severity: 3,
    message: 'connection failed: 500 internal server error',
    raw: '<11>1 2026-08-29T14:30:00.000Z proxmox-01 pveproxy 1234 - - connection failed: 500 internal server error',
  };

  const contextLogs: LogEntry[] = [
    {
      id: 98,
      timestamp: '2026-08-29T14:29:50.000Z',
      received_at: '2026-08-29T14:29:50.100Z',
      source_ip: '192.168.1.50',
      source_alias: 'proxmox-01',
      app_name: 'corosync',
      facility: 1,
      severity: 6,
      message: 'totem message transmission token received',
      raw: 'raw corosync',
    },
    {
      id: 99,
      timestamp: '2026-08-29T14:29:55.000Z',
      received_at: '2026-08-29T14:29:55.100Z',
      source_ip: '192.168.1.50',
      source_alias: 'proxmox-01',
      app_name: 'pveproxy',
      facility: 1,
      severity: 4,
      message: 'worker connecting to cluster daemon',
      raw: 'raw pveproxy 1',
    },
    sampleLog,
    {
      id: 101,
      timestamp: '2026-08-29T14:30:05.000Z',
      received_at: '2026-08-29T14:30:05.100Z',
      source_ip: '192.168.1.50',
      source_alias: 'proxmox-01',
      app_name: 'pvedaemon',
      facility: 1,
      severity: 3,
      message: 'task worker died with error code 1',
      raw: 'raw pvedaemon',
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders "Modify Host Alias" when isHostAliased is true (Item #11)', () => {
    const handleAddAlias = vi.fn();
    render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onAddAlias={handleAddAlias}
        isHostAliased={true}
      />
    );

    const modifyBtn = screen.getByRole('button', { name: /Modify Host Alias/i });
    expect(modifyBtn).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Add Host Alias$/i })).toBeNull();

    fireEvent.click(modifyBtn);
    expect(handleAddAlias).toHaveBeenCalledWith('192.168.1.50');
  });

  it('labels each timestamp with the UTC offset in force at that moment', () => {
    // Logged at 00:59:59 UTC (still BST), received at 01:00:01 UTC (after UK clocks went back)
    const acrossChange: LogEntry = {
      ...sampleLog,
      timestamp: '2026-10-25T00:59:59.000Z',
      received_at: '2026-10-25T01:00:01.000Z',
    };
    render(
      <LogDetailModal
        log={acrossChange}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        timeZone="Europe/London"
      />
    );

    expect(screen.getByText('Timestamp (UTC+01:00)')).toBeInTheDocument();
    expect(screen.getByText('2026-10-25 01:59:59.000')).toBeInTheDocument();
    expect(screen.getByText('Received At (UTC+00:00)')).toBeInTheDocument();
    expect(screen.getByText('2026-10-25 01:00:01.000')).toBeInTheDocument();
  });

  it('renders "Add Host Alias" when isHostAliased is false', () => {
    const unaliasedLog: LogEntry = {
      ...sampleLog,
      source_alias: '192.168.1.50',
    };
    const handleAddAlias = vi.fn();
    render(
      <LogDetailModal
        log={unaliasedLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onAddAlias={handleAddAlias}
        isHostAliased={false}
      />
    );

    const addBtn = screen.getByRole('button', { name: /Add Host Alias/i });
    expect(addBtn).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Modify Host Alias/i })).toBeNull();

    fireEvent.click(addBtn);
    expect(handleAddAlias).toHaveBeenCalledWith('192.168.1.50');
  });

  it('automatically infers "Modify Host Alias" when log has source_alias different from source_ip', () => {
    const handleAddAlias = vi.fn();
    render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onAddAlias={handleAddAlias}
      />
    );

    const modifyBtn = screen.getByRole('button', { name: /Modify Host Alias/i });
    expect(modifyBtn).toBeInTheDocument();
  });

  it('loads surrounding context and calls fetchLogContext with same_app=false by default (Item #9)', async () => {
    const fetchSpy = vi.spyOn(logsApi, 'fetchLogContext').mockResolvedValue({
      target_id: sampleLog.id,
      logs: contextLogs,
    });

    render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        isHostAliased={true}
      />
    );

    const loadContextBtn = screen.getByRole('button', { name: /Load Surrounding Context/i });
    fireEvent.click(loadContextBtn);

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(100, 10, false);
    });

    expect(screen.getByText(/totem message transmission token received/i)).toBeInTheDocument();
    expect(screen.getByText(/task worker died with error code 1/i)).toBeInTheDocument();
  });

  it('toggling "Same App Only" reloads context with same_app=true (Item #9)', async () => {
    const fetchSpy = vi.spyOn(logsApi, 'fetchLogContext').mockResolvedValue({
      target_id: sampleLog.id,
      logs: contextLogs.filter((l) => l.app_name === 'pveproxy'),
    });

    render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        isHostAliased={true}
      />
    );

    // Open context inspector
    fireEvent.click(screen.getByRole('button', { name: /Load Surrounding Context/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(100, 10, false);
    });

    // Toggle same app checkbox
    const sameAppCheckbox = screen.getByRole('checkbox', { name: /Same App Only/i });
    expect(sameAppCheckbox).not.toBeChecked();

    fireEvent.click(sameAppCheckbox);

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(100, 10, true);
    });
  });

  it('renders "Inspect Target + Context (N logs)" and transfers logs to AI analysis (Item #8)', async () => {
    vi.spyOn(logsApi, 'fetchLogContext').mockResolvedValue({
      target_id: sampleLog.id,
      logs: contextLogs,
    });

    const handleInspectWithContext = vi.fn();
    const handleClose = vi.fn();

    render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={handleClose}
        onExplainWithAi={vi.fn()}
        onInspectWithContext={handleInspectWithContext}
        aiEnabled={true}
        isHostAliased={true}
      />
    );

    // Open context inspector
    fireEvent.click(screen.getByRole('button', { name: /Load Surrounding Context/i }));

    // Wait for context logs to load and the action button to appear
    const inspectContextBtn = await screen.findByRole('button', {
      name: /Inspect Target \+ Context \(4 logs\)/i,
    });
    expect(inspectContextBtn).toBeInTheDocument();
    expect(inspectContextBtn).toHaveAttribute('title', 'Add Context to AI Analysis');

    // Clicking it should transfer all 4 logs (sorted chronologically) and close modal
    fireEvent.click(inspectContextBtn);

    expect(handleInspectWithContext).toHaveBeenCalledTimes(1);
    const transferredLogs = handleInspectWithContext.mock.calls[0][0];
    expect(transferredLogs).toHaveLength(4);
    expect(transferredLogs.map((l: LogEntry) => l.id)).toEqual([98, 99, 100, 101]);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('filters out any logs not matching the target host to enforce single-host constraint (Item #8)', async () => {
    const mixedHostLogs: LogEntry[] = [
      ...contextLogs,
      {
        id: 102,
        timestamp: '2026-08-29T14:30:10.000Z',
        received_at: '2026-08-29T14:30:10.100Z',
        source_ip: '192.168.1.99',
        source_alias: 'other-host',
        app_name: 'nginx',
        facility: 1,
        severity: 3,
        message: 'disparate host log',
        raw: 'raw other host',
      },
    ];

    vi.spyOn(logsApi, 'fetchLogContext').mockResolvedValue({
      target_id: sampleLog.id,
      logs: mixedHostLogs,
    });

    const handleInspectWithContext = vi.fn();

    render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onInspectWithContext={handleInspectWithContext}
        aiEnabled={true}
        isHostAliased={true}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Load Surrounding Context/i }));

    const inspectContextBtn = await screen.findByRole('button', {
      name: /Inspect Target \+ Context \(4 logs\)/i,
    });

    fireEvent.click(inspectContextBtn);

    expect(handleInspectWithContext).toHaveBeenCalledTimes(1);
    const transferredLogs = handleInspectWithContext.mock.calls[0][0];
    // other-host (id 102) was strictly excluded
    expect(transferredLogs).toHaveLength(4);
    expect(transferredLogs.every((l: LogEntry) => l.source_alias === 'proxmox-01')).toBe(true);
  });

  it('renders Create Drop Rule button and calls callback when clicked', () => {
    const handleCreateDropRule = vi.fn();

    render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onCreateDropRule={handleCreateDropRule}
      />
    );

    const dropRuleBtn = screen.getByRole('button', { name: /Create Drop Rule/i });
    expect(dropRuleBtn).toBeInTheDocument();

    fireEvent.click(dropRuleBtn);
    expect(handleCreateDropRule).toHaveBeenCalledWith(sampleLog);
  });

  it('renders Newer and Older log buttons and triggers navigation callbacks', () => {
    const handlePrevious = vi.fn();
    const handleNext = vi.fn();

    const { rerender } = render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onNavigateNewer={handlePrevious}
        onNavigateOlder={handleNext}
        hasNewerLog={true}
        hasOlderLog={true}
      />
    );

    const prevBtn = screen.getByRole('button', { name: /Newer log/i });
    const nextBtn = screen.getByRole('button', { name: /Older log/i });

    expect(prevBtn).toBeInTheDocument();
    expect(nextBtn).toBeInTheDocument();
    expect(prevBtn).toBeEnabled();
    expect(nextBtn).toBeEnabled();

    fireEvent.click(prevBtn);
    expect(handlePrevious).toHaveBeenCalledTimes(1);

    fireEvent.click(nextBtn);
    expect(handleNext).toHaveBeenCalledTimes(1);

    // Disable state when boundaries reached
    rerender(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onNavigateNewer={handlePrevious}
        onNavigateOlder={handleNext}
        hasNewerLog={false}
        hasOlderLog={false}
      />
    );

    expect(prevBtn).toBeDisabled();
    expect(nextBtn).toBeDisabled();
  });

  it('renders cleaned message payload and Raw Container Output for Docker logs', () => {
    const dockerLog: LogEntry = {
      id: 935138,
      timestamp: '2026-10-03T15:56:31.293875+00:00',
      received_at: '2026-10-03T15:56:31.294203+00:00',
      source_ip: 'docker',
      source_alias: 'Docker',
      app_name: 'Paperless-GPT',
      facility: 1,
      severity: 6,
      message: 'time="2026-10-03T15:56:31Z" level=info msg="Successfully refreshed custom fields cache with 0 fields."',
      raw: 'time="2026-10-03T15:56:31Z" level=info msg="Successfully refreshed custom fields cache with 0 fields."',
    };

    render(
      <LogDetailModal
        log={dockerLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
      />
    );

    // Cleaned payload extracted
    expect(screen.getByText('Successfully refreshed custom fields cache with 0 fields.')).toBeInTheDocument();
    // Dynamic container label
    expect(screen.getByText('Raw Container Output')).toBeInTheDocument();
    expect(screen.queryByText('Raw Syslog Packet')).toBeNull();
  });

  it('renders Raw Syslog Packet label for non-Docker logs', () => {
    render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
      />
    );

    expect(screen.getByText('Raw Syslog Packet')).toBeInTheDocument();
    expect(screen.queryByText('Raw Container Output')).toBeNull();
  });

  it('renders "Edit Docker Host" for active Docker endpoint log and calls onEditDockerHost', () => {
    const dockerLog: LogEntry = {
      id: 201,
      timestamp: '2026-10-12T10:00:00Z',
      received_at: '2026-10-12T10:00:00Z',
      source_ip: '192.168.1.50',
      source_alias: 'unraid-docker',
      app_name: 'plex',
      facility: 1,
      severity: 6,
      message: 'Container started',
      raw: 'Container started',
    };
    const mockEndpoints = [
      {
        id: 2,
        name: 'Unraid Docker',
        url: 'tcp://192.168.1.50:2375',
        source_alias: 'unraid-docker',
        enabled: true,
        tls_enabled: false,
        ca_cert: null,
        client_cert: null,
        has_client_key: false,
        exclude_containers: '',
        created_at: '2026-10-12T00:00:00Z',
        updated_at: '2026-10-12T00:00:00Z',
        is_connected: true,
        active_containers: 5,
      },
    ];

    const handleEditDocker = vi.fn();
    render(
      <LogDetailModal
        log={dockerLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onEditDockerHost={handleEditDocker}
        dockerEndpoints={mockEndpoints}
      />
    );

    const editBtn = screen.getByRole('button', { name: /Edit Docker Host/i });
    expect(editBtn).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Modify Host Alias/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Add Host Alias/i })).toBeNull();

    fireEvent.click(editBtn);
    expect(handleEditDocker).toHaveBeenCalledWith(2);
  });

  it('renders "Configure Docker Host" for a log from the deleted local Docker host and calls onManageDeletedDockerHost', () => {
    const deletedDockerLog: LogEntry = {
      id: 202,
      timestamp: '2026-10-12T10:00:00Z',
      received_at: '2026-10-12T10:00:00Z',
      source_ip: 'docker',
      source_alias: 'old-docker',
      app_name: 'plex',
      facility: 1,
      severity: 6,
      message: 'Container started',
      raw: 'Container started',
    };

    const handleManageDeleted = vi.fn();
    render(
      <LogDetailModal
        log={deletedDockerLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onManageDeletedDockerHost={handleManageDeleted}
        dockerEndpoints={[]}
      />
    );

    const configBtn = screen.getByRole('button', { name: /Configure Docker Host/i });
    expect(configBtn).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Modify Host Alias/i })).toBeNull();
    expect(screen.getByText('Raw Container Output')).toBeInTheDocument();

    fireEvent.click(configBtn);
    expect(handleManageDeleted).toHaveBeenCalledWith({
      name: 'old-docker',
      ip: 'docker',
    });
  });

  it('treats a facility 1 log sent over HTTP ingest as a host alias log, not a Docker log', () => {
    const httpLog: LogEntry = {
      id: 203,
      timestamp: '2026-10-21T02:00:00Z',
      received_at: '2026-10-21T02:00:00Z',
      source_ip: '192.168.10.12',
      source_alias: 'pve-node2',
      app_name: 'vzdump',
      facility: 1,
      severity: 6,
      message: 'INFO: Finished Backup of VM 100',
      raw: 'INFO: Finished Backup of VM 100',
    };

    const handleAddAlias = vi.fn();
    render(
      <LogDetailModal
        log={httpLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onAddAlias={handleAddAlias}
        onEditDockerHost={vi.fn()}
        onManageDeletedDockerHost={vi.fn()}
        dockerEndpoints={[]}
      />
    );

    expect(screen.queryByRole('button', { name: /Configure Docker Host/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Edit Docker Host/i })).toBeNull();
    expect(screen.getByText('Raw Log Record')).toBeInTheDocument();
    expect(screen.queryByText('Raw Container Output')).toBeNull();
    expect(screen.queryByText('Raw Syslog Packet')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Modify Host Alias/i }));
    expect(handleAddAlias).toHaveBeenCalledWith('192.168.10.12');
  });

  it('keeps an HTTP-ingested log from a Docker host machine out of that Docker host unless the alias matches', () => {
    const httpLog: LogEntry = {
      id: 204,
      timestamp: '2026-10-21T02:00:00Z',
      received_at: '2026-10-21T02:00:00Z',
      source_ip: '192.168.1.50',
      source_alias: 'unraid',
      app_name: 'backup-script',
      facility: 1,
      severity: 6,
      message: 'done',
      raw: 'done',
    };
    const mockEndpoints = [
      {
        id: 2,
        name: 'Unraid Docker',
        url: 'tcp://192.168.1.50:2375',
        source_alias: 'unraid-docker',
        enabled: true,
        tls_enabled: false,
        ca_cert: null,
        client_cert: null,
        has_client_key: false,
        exclude_containers: '',
        created_at: '2026-10-12T00:00:00Z',
        updated_at: '2026-10-12T00:00:00Z',
        is_connected: true,
        active_containers: 5,
      },
    ];

    render(
      <LogDetailModal
        log={httpLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onAddAlias={vi.fn()}
        onEditDockerHost={vi.fn()}
        dockerEndpoints={mockEndpoints}
      />
    );

    expect(screen.queryByRole('button', { name: /Edit Docker Host/i })).toBeNull();
    expect(screen.getByRole('button', { name: /Modify Host Alias/i })).toBeInTheDocument();
    expect(screen.getByText('Raw Log Record')).toBeInTheDocument();
  });

  it('treats a facility 1 syslog packet from a configured Docker host machine as a syslog log', () => {
    const syslogLog: LogEntry = {
      ...sampleLog,
      id: 205,
      source_ip: '192.168.1.50',
      source_alias: 'unraid',
    };
    const mockEndpoints = [
      {
        id: 2,
        name: 'Unraid Docker',
        url: 'tcp://192.168.1.50:2375',
        source_alias: 'unraid',
        enabled: true,
        tls_enabled: false,
        ca_cert: null,
        client_cert: null,
        has_client_key: false,
        exclude_containers: '',
        created_at: '2026-10-12T00:00:00Z',
        updated_at: '2026-10-12T00:00:00Z',
        is_connected: true,
        active_containers: 5,
      },
    ];

    render(
      <LogDetailModal
        log={syslogLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onAddAlias={vi.fn()}
        onEditDockerHost={vi.fn()}
        onManageDeletedDockerHost={vi.fn()}
        dockerEndpoints={mockEndpoints}
      />
    );

    expect(screen.queryByRole('button', { name: /Docker Host/i })).toBeNull();
    expect(screen.getByRole('button', { name: /Modify Host Alias/i })).toBeInTheDocument();
    expect(screen.getByText('Raw Syslog Packet')).toBeInTheDocument();
  });

  it('does not offer a Docker action for a log from an unknown host whose alias merely contains "docker"', () => {
    const log: LogEntry = {
      id: 206,
      timestamp: '2026-10-21T02:00:00Z',
      received_at: '2026-10-21T02:00:00Z',
      source_ip: '10.0.0.9',
      source_alias: 'docker-build-box',
      app_name: 'ci',
      facility: 1,
      severity: 6,
      message: 'build ok',
      raw: 'build ok',
    };

    render(
      <LogDetailModal
        log={log}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onAddAlias={vi.fn()}
        onManageDeletedDockerHost={vi.fn()}
        dockerEndpoints={[]}
      />
    );

    expect(screen.queryByRole('button', { name: /Configure Docker Host/i })).toBeNull();
    expect(screen.getByText('Raw Log Record')).toBeInTheDocument();
  });
});


describe('LogDetailModal context handling (B-25)', () => {
  const mk = (id: number): LogEntry => ({
    id,
    timestamp: '2026-08-29T14:30:00.000Z',
    received_at: '2026-08-29T14:30:00.100Z',
    source_ip: '10.0.0.1',
    source_alias: 'host-a',
    app_name: 'app',
    facility: 1,
    severity: 3,
    message: `message ${id}`,
    raw: `raw ${id}`,
  });

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows "Could not load context" when the context request fails', async () => {
    vi.spyOn(logsApi, 'fetchLogContext').mockRejectedValue(new Error('boom'));
    render(<LogDetailModal log={mk(1)} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    fireEvent.click(screen.getByText(/Load Surrounding Context/i));
    expect(await screen.findByText('Could not load context')).toBeInTheDocument();
  });

  it('ignores a slow context response for a previous log', async () => {
    let resolveFirst: (v: any) => void = () => {};
    const spy = vi.spyOn(logsApi, 'fetchLogContext').mockImplementation((id: number) => {
      if (id === 1) {
        return new Promise((resolve) => {
          resolveFirst = resolve;
        });
      }
      return Promise.resolve({ target_id: 2, logs: [mk(2), { ...mk(77), message: 'context for two' }] });
    });
    const { rerender } = render(
      <LogDetailModal log={mk(1)} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} />
    );
    fireEvent.click(screen.getByText(/Load Surrounding Context/i));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(1, 10, false));
    rerender(<LogDetailModal log={mk(2)} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    expect(await screen.findByText('context for two')).toBeInTheDocument();
    resolveFirst({ target_id: 1, logs: [{ ...mk(55), message: 'stale context for one' }] });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText('stale context for one')).toBeNull();
    expect(screen.getByText('context for two')).toBeInTheDocument();
  });
});

describe('LogDetailModal AI setting, Newer / Older steps and context app names (WP1)', () => {
  const log: LogEntry = {
    id: 500,
    timestamp: '2026-08-29T14:30:00.000Z',
    received_at: '2026-08-29T14:30:00.100Z',
    source_ip: '192.168.1.50',
    source_alias: 'proxmox-01',
    app_name: 'pveproxy',
    facility: 1,
    severity: 3,
    message: 'connection failed',
    raw: 'connection failed',
  };
  const ctx: LogEntry[] = [
    { ...log, id: 499, app_name: 'corosync-cluster-engine-daemon', message: 'token received' },
    log,
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const renderNav = (overrides: Record<string, unknown> = {}) => {
    const onNewer = vi.fn();
    const onOlder = vi.fn();
    const utils = render(
      <LogDetailModal
        log={log}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onNavigateNewer={onNewer}
        onNavigateOlder={onOlder}
        hasNewerLog={true}
        hasOlderLog={true}
        {...overrides}
      />
    );
    return { ...utils, onNewer, onOlder };
  };

  it('renders a muted "Explain with AI" entry with a hint when AI is off, and still calls the handler', () => {
    const onExplain = vi.fn();
    render(<LogDetailModal log={log} isOpen={true} onClose={vi.fn()} onExplainWithAi={onExplain} aiEnabled={false} />);
    const btn = screen.getByRole('button', { name: /Explain with AI/i });
    expect(btn).toHaveAttribute('title', 'AI features are turned off in Settings');
    expect(btn.className).not.toContain('bg-accent-600');
    fireEvent.click(btn);
    expect(onExplain).toHaveBeenCalledWith(log);
  });

  it('renders the primary "Explain with AI" button without the hint when AI is on', () => {
    render(<LogDetailModal log={log} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} aiEnabled={true} />);
    const btn = screen.getByRole('button', { name: /Explain with AI/i });
    expect(btn).not.toHaveAttribute('title');
    expect(btn.className).toContain('bg-accent-600');
  });

  it('hides "Inspect Target + Context" when AI is off', async () => {
    vi.spyOn(logsApi, 'fetchLogContext').mockResolvedValue({ target_id: log.id, logs: ctx });
    render(<LogDetailModal log={log} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} aiEnabled={false} />);
    fireEvent.click(screen.getByRole('button', { name: /Load Surrounding Context/i }));
    await screen.findByText('token received');
    expect(screen.queryByRole('button', { name: /Inspect Target \+ Context/i })).toBeNull();
  });

  it('shows "Inspect Target + Context" when AI is on', async () => {
    vi.spyOn(logsApi, 'fetchLogContext').mockResolvedValue({ target_id: log.id, logs: ctx });
    render(<LogDetailModal log={log} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} aiEnabled={true} />);
    fireEvent.click(screen.getByRole('button', { name: /Load Surrounding Context/i }));
    expect(await screen.findByRole('button', { name: /Inspect Target \+ Context \(2 logs\)/i })).toBeInTheDocument();
  });

  it('labels the steps Newer and Older with their keyboard hints', () => {
    renderNav();
    expect(screen.getByRole('button', { name: 'Newer log' })).toHaveAttribute('title', 'Newer log (Up or K)');
    expect(screen.getByRole('button', { name: 'Older log' })).toHaveAttribute('title', 'Older log (Down or J)');
    expect(screen.queryByRole('button', { name: /Previous log/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Next log/i })).toBeNull();
  });

  it('maps ArrowUp and K to Newer, ArrowDown and J to Older', () => {
    const { onNewer, onOlder } = renderNav();
    fireEvent.keyDown(document.body, { key: 'ArrowUp' });
    fireEvent.keyDown(document.body, { key: 'k' });
    expect(onNewer).toHaveBeenCalledTimes(2);
    expect(onOlder).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: 'ArrowDown' });
    fireEvent.keyDown(document.body, { key: 'j' });
    expect(onOlder).toHaveBeenCalledTimes(2);
    expect(onNewer).toHaveBeenCalledTimes(2);
  });

  it('does nothing at the ends of the list: buttons disabled and keys ignored', () => {
    const { onNewer, onOlder } = renderNav({ hasNewerLog: false, hasOlderLog: false });
    expect(screen.getByRole('button', { name: 'Newer log' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Older log' })).toBeDisabled();
    fireEvent.keyDown(document.body, { key: 'ArrowUp' });
    fireEvent.keyDown(document.body, { key: 'ArrowDown' });
    fireEvent.keyDown(document.body, { key: 'k' });
    fireEvent.keyDown(document.body, { key: 'j' });
    expect(onNewer).not.toHaveBeenCalled();
    expect(onOlder).not.toHaveBeenCalled();
  });

  it('ignores the keys while typing in a text field, select or editable element', () => {
    const { onNewer, onOlder } = renderNav();
    const input = document.createElement('input');
    const textarea = document.createElement('textarea');
    const select = document.createElement('select');
    const editable = document.createElement('div');
    editable.setAttribute('contenteditable', 'true');
    document.body.append(input, textarea, select, editable);
    try {
      for (const el of [input, textarea, select, editable]) {
        fireEvent.keyDown(el, { key: 'k' });
        fireEvent.keyDown(el, { key: 'j' });
        fireEvent.keyDown(el, { key: 'ArrowUp' });
        fireEvent.keyDown(el, { key: 'ArrowDown' });
      }
      expect(onNewer).not.toHaveBeenCalled();
      expect(onOlder).not.toHaveBeenCalled();
    } finally {
      input.remove();
      textarea.remove();
      select.remove();
      editable.remove();
    }
  });

  it('ignores the keys while another dialog is open on top of the drawer', () => {
    const { onNewer, onOlder } = renderNav();
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    document.body.append(dialog);
    try {
      fireEvent.keyDown(document.body, { key: 'ArrowUp' });
      fireEvent.keyDown(document.body, { key: 'j' });
      expect(onNewer).not.toHaveBeenCalled();
      expect(onOlder).not.toHaveBeenCalled();
    } finally {
      dialog.remove();
    }
    fireEvent.keyDown(document.body, { key: 'ArrowUp' });
    expect(onNewer).toHaveBeenCalledTimes(1);
  });

  it('ignores the keys once the drawer is closed', () => {
    const { onNewer, rerender } = renderNav();
    rerender(
      <LogDetailModal log={log} isOpen={false} onClose={vi.fn()} onExplainWithAi={vi.fn()} onNavigateNewer={onNewer} hasNewerLog={true} />
    );
    fireEvent.keyDown(document.body, { key: 'ArrowUp' });
    expect(onNewer).not.toHaveBeenCalled();
  });

  it('shows each context row app name, truncated with the full name as a tooltip', async () => {
    vi.spyOn(logsApi, 'fetchLogContext').mockResolvedValue({ target_id: log.id, logs: ctx });
    render(<LogDetailModal log={log} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Load Surrounding Context/i }));
    await screen.findByText('token received');
    const apps = screen.getAllByTestId('context-app-name');
    expect(apps.map((a) => a.textContent)).toEqual(['corosync-cluster-engine-daemon', 'pveproxy']);
    expect(apps[0]).toHaveAttribute('title', 'corosync-cluster-engine-daemon');
    expect(apps[0].className).toContain('truncate');
  });

  it('hides the context app column when "Same App Only" is checked', async () => {
    vi.spyOn(logsApi, 'fetchLogContext').mockResolvedValue({ target_id: log.id, logs: [log] });
    render(<LogDetailModal log={log} isOpen={true} onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Load Surrounding Context/i }));
    await waitFor(() => expect(screen.getAllByTestId('context-app-name').length).toBe(1));
    fireEvent.click(screen.getByRole('checkbox'));
    await waitFor(() => expect(logsApi.fetchLogContext).toHaveBeenLastCalledWith(log.id, 10, true));
    await screen.findAllByText('connection failed');
    expect(screen.queryByTestId('context-app-name')).toBeNull();
  });
});
