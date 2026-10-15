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

  it('renders Previous and Next log buttons and triggers navigation callbacks', () => {
    const handlePrevious = vi.fn();
    const handleNext = vi.fn();

    const { rerender } = render(
      <LogDetailModal
        log={sampleLog}
        isOpen={true}
        onClose={vi.fn()}
        onExplainWithAi={vi.fn()}
        onNavigatePrevious={handlePrevious}
        onNavigateNext={handleNext}
        hasPreviousLog={true}
        hasNextLog={true}
      />
    );

    const prevBtn = screen.getByRole('button', { name: /Previous log/i });
    const nextBtn = screen.getByRole('button', { name: /Next log/i });

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
        onNavigatePrevious={handlePrevious}
        onNavigateNext={handleNext}
        hasPreviousLog={false}
        hasNextLog={false}
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

  it('renders "Configure Docker Host" for deleted Docker host and calls onManageDeletedDockerHost', () => {
    const deletedDockerLog: LogEntry = {
      id: 202,
      timestamp: '2026-10-12T10:00:00Z',
      received_at: '2026-10-12T10:00:00Z',
      source_ip: '192.168.1.50',
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

    fireEvent.click(configBtn);
    expect(handleManageDeleted).toHaveBeenCalledWith({
      name: 'old-docker',
      ip: '192.168.1.50',
    });
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
