import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DropRulesCard } from '../components/alerts/tabs/DropRulesTab.tsx';
import * as dropRulesApi from '../api/dropRules.ts';
import { DropRule } from '../types.ts';
import { stubDownloads } from './downloadStubs.ts';

describe('DropRulesCard Component', () => {
  const mockRules: DropRule[] = [
    {
      id: 1,
      name: 'Drop DHCPACK',
      source_pattern: '192.168.1.*',
      app_pattern: 'dnsmasq',
      message_pattern: 'DHCPACK',
      is_regex: false,
      is_enabled: true,
      severity_threshold: 6,
      dropped_count: 42,
      created_at: '2026-09-18T10:00:00Z',
    },
    {
      id: 2,
      name: 'Drop Probe Requests',
      source_pattern: null,
      app_pattern: null,
      message_pattern: 'probe request',
      is_regex: true,
      is_enabled: false,
      severity_threshold: null,
      dropped_count: 10,
      created_at: '2026-09-18T10:05:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(dropRulesApi, 'fetchDropRules').mockResolvedValue([...mockRules]);
  });

  it('renders drop rules list with statuses and counts', async () => {
    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Ingestion Drop Rules')).toBeInTheDocument();
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
      expect(screen.getByText('Drop Probe Requests')).toBeInTheDocument();
      expect(screen.getByText('192.168.1.*')).toBeInTheDocument();
      expect(screen.getByText('dnsmasq')).toBeInTheDocument();
      expect(screen.getByText('Info (6) and less severe')).toBeInTheDocument();
    });

    expect(screen.getByText('42 dropped')).toBeInTheDocument();
    expect(screen.getByText('10 dropped')).toBeInTheDocument();
  });

  it('toggles rule enabled/disabled status', async () => {
    const updateSpy = vi.spyOn(dropRulesApi, 'updateDropRule').mockResolvedValue({
      ...mockRules[0],
      is_enabled: false,
    });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
    });

    const activeBtn = screen.getByText('Active');
    fireEvent.click(activeBtn);

    expect(updateSpy).toHaveBeenCalledWith(1, { is_enabled: false });
    // The card shows the saved state
    expect(await screen.findByText('Disabled')).toBeInTheDocument();
  });

  it('allows editing an existing rule and resetting its counter inside the modal', async () => {
    const resetSpy = vi.spyOn(dropRulesApi, 'resetDropRuleCounter').mockResolvedValue({
      ...mockRules[0],
      dropped_count: 0,
    });
    const updateSpy = vi.spyOn(dropRulesApi, 'updateDropRule').mockResolvedValue({
      ...mockRules[0],
      message_pattern: 'DHCPACK-UPDATED',
      dropped_count: 0,
    });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
    });

    // Click pencil edit icon
    const editBtn = screen.getByLabelText('Edit rule 1');
    fireEvent.click(editBtn);

    // Verify modal is in edit mode
    expect(screen.getByText('Edit Ingestion Drop Rule')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Drop DHCPACK')).toBeInTheDocument();
    expect(screen.getAllByDisplayValue('DHCPACK').length).toBeGreaterThanOrEqual(1);

    // Verify reset counter button inside modal
    const resetCounterBtn = screen.getByRole('button', { name: /Reset counter to 0/i });
    expect(resetCounterBtn).toBeInTheDocument();
    fireEvent.click(resetCounterBtn);

    await waitFor(() => {
      expect(resetSpy).toHaveBeenCalledWith(1);
    });

    // Change message pattern and save
    const msgInput = screen.getByPlaceholderText(/e\.g\. DHCPACK/i);
    fireEvent.change(msgInput, { target: { value: 'DHCPACK-UPDATED' } });

    const saveBtn = screen.getByRole('button', { name: 'Save Changes' });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(1, expect.objectContaining({
        name: 'Drop DHCPACK',
        message_pattern: 'DHCPACK-UPDATED',
      }));
      expect(screen.getByText('Drop rule updated successfully.')).toBeInTheDocument();
    });
  });

  it('requires confirmation before deleting a drop rule and allows cancel', async () => {
    const deleteSpy = vi.spyOn(dropRulesApi, 'deleteDropRule').mockResolvedValue({ status: 'ok' });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
    });

    const delBtn = screen.getByLabelText('Delete rule 1');
    fireEvent.click(delBtn);

    // Confirmation dialog should be displayed
    expect(screen.getByText('Delete this drop rule?')).toBeInTheDocument();
    expect(screen.getByText('Incoming matching logs will no longer be discarded and will resume being stored in SQLite and indexed by FTS5.')).toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    // Cancel deletion
    const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
    fireEvent.click(cancelBtn);
    expect(screen.queryByText('Delete this drop rule?')).not.toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();
    expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();

    // Open confirmation again and confirm
    fireEvent.click(delBtn);
    expect(screen.getByText('Delete this drop rule?')).toBeInTheDocument();

    const confirmDelBtn = screen.getByRole('button', { name: 'Delete Rule' });
    fireEvent.click(confirmDelBtn);

    expect(deleteSpy).toHaveBeenCalledWith(1);
    await waitFor(() => {
      expect(screen.queryByText('Drop DHCPACK')).not.toBeInTheDocument();
    });
  });

  it('opens add rule modal, performs dry-run test, and saves rule', async () => {
    const testSpy = vi.spyOn(dropRulesApi, 'testDropRule').mockResolvedValue({
      matched: true,
      error: null,
    });
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({
      id: 3,
      name: 'Traefik Healthcheck',
      source_pattern: '10.0.0.*',
      app_pattern: 'traefik',
      message_pattern: 'healthcheck',
      is_regex: false,
      is_enabled: true,
      dropped_count: 0,
      created_at: '2026-09-18T10:10:00Z',
    });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
    });

    // Open Modal
    fireEvent.click(screen.getByText('New Rule'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // Fill form
    const nameInput = screen.getByPlaceholderText(/e\.g\. CRON Chatter/i);
    fireEvent.change(nameInput, { target: { value: 'Traefik Healthcheck' } });

    const msgInput = screen.getByPlaceholderText(/e\.g\. DHCPACK/i);
    fireEvent.change(msgInput, { target: { value: 'healthcheck' } });

    const sampleMsg = screen.getByPlaceholderText('Paste a representative log line here...');
    fireEvent.change(sampleMsg, { target: { value: 'GET /healthcheck 200' } });

    // Execute dry run test
    const testBtn = screen.getByText('Dry-Run Test');
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalled();
      expect(screen.getByText(/Rule matched/i)).toBeInTheDocument();
    });

    // Submit form
    const saveBtn = screen.getByRole('button', { name: 'Create Rule' });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(expect.objectContaining({
        name: 'Traefik Healthcheck',
        message_pattern: 'healthcheck',
        is_regex: false,
        source_pattern: undefined,
        app_pattern: undefined,
        severity_threshold: undefined,
        is_enabled: true,
      }));
      expect(screen.getByText('Traefik Healthcheck')).toBeInTheDocument();
    });
  });

  it('allows selecting host/app from dropdown or entering custom wildcard', async () => {
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({
      id: 4,
      name: 'Router Timeout Rule',
      source_pattern: 'router*',
      app_pattern: 'traefik',
      message_pattern: 'timeout',
      is_regex: false,
      is_enabled: true,
      dropped_count: 0,
      created_at: '2026-09-18T10:15:00Z',
    });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Rule'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText(/e\.g\. CRON Chatter/i);
    fireEvent.change(nameInput, { target: { value: 'Router Timeout Rule' } });

    const msgInput = screen.getByPlaceholderText(/e\.g\. DHCPACK/i);
    fireEvent.change(msgInput, { target: { value: 'timeout' } });

    // Host: Select custom wildcard
    const hostSelect = screen.getByLabelText(/Host \/ IP Filter/i) || screen.getAllByRole('combobox')[0];
    fireEvent.change(hostSelect, { target: { value: '__custom__' } });

    // Custom input appears
    const customHostInput = screen.getByPlaceholderText('e.g. 192.168.1.*, router*, *dns*');
    fireEvent.change(customHostInput, { target: { value: 'router*' } });

    // App: Select custom wildcard
    const appSelect = screen.getByLabelText(/Application or Container/i) || screen.getAllByRole('combobox')[1];
    fireEvent.change(appSelect, { target: { value: '__custom__' } });

    const customAppInput = screen.getByPlaceholderText('e.g. dnsmasq, smartbulb*, traefik');
    fireEvent.change(customAppInput, { target: { value: 'traefik' } });

    // Submit
    const saveBtn = screen.getByRole('button', { name: 'Create Rule' });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(expect.objectContaining({
        name: 'Router Timeout Rule',
        message_pattern: 'timeout',
        is_regex: false,
        source_pattern: 'router*',
        app_pattern: 'traefik',
        severity_threshold: undefined,
        is_enabled: true,
      }));
    });
  });

  it('allows dropping all logs for an app with message pattern * or empty', async () => {
    const testSpy = vi.spyOn(dropRulesApi, 'testDropRule').mockResolvedValue({
      matched: true,
      error: null,
    });
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({
      id: 5,
      name: 'Drop All PveDaemon',
      source_pattern: null,
      app_pattern: 'pvedaemon',
      message_pattern: '*',
      is_regex: false,
      is_enabled: true,
      dropped_count: 0,
      created_at: '2026-09-18T10:20:00Z',
    });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Rule'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText(/e\.g\. CRON Chatter/i);
    fireEvent.change(nameInput, { target: { value: 'Drop All PveDaemon' } });

    // Select custom app: pvedaemon
    const appSelect = screen.getByLabelText(/Application or Container/i);
    fireEvent.change(appSelect, { target: { value: '__custom__' } });

    const customAppInput = screen.getByPlaceholderText('e.g. dnsmasq, smartbulb*, traefik');
    fireEvent.change(customAppInput, { target: { value: 'pvedaemon' } });

    // Set message pattern to * (or leave blank)
    const msgInput = screen.getByPlaceholderText(/e\.g\. DHCPACK/i);
    fireEvent.change(msgInput, { target: { value: '*' } });

    // Test with sample message
    const sampleMsg = screen.getByPlaceholderText('Paste a representative log line here...');
    fireEvent.change(sampleMsg, { target: { value: '<14>1 2026-09-16T10:10:00.323649+00:00 pve-node1 pvedaemon 1024 ID42 VM 104 backup completed' } });

    const testBtn = screen.getByText('Dry-Run Test');
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalledWith(expect.objectContaining({
        app_pattern: 'pvedaemon',
        message_pattern: '*',
        sample_app: 'pvedaemon',
      }));
      expect(screen.getByText(/Rule matched/i)).toBeInTheDocument();
    });

    // Submit rule
    const saveBtn = screen.getByRole('button', { name: 'Create Rule' });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(expect.objectContaining({
        name: 'Drop All PveDaemon',
        message_pattern: '*',
        is_regex: false,
        source_pattern: undefined,
        app_pattern: 'pvedaemon',
        severity_threshold: undefined,
        is_enabled: true,
      }));
    });
  });

  it('creates a drop rule with severity threshold', async () => {
    const testSpy = vi.spyOn(dropRulesApi, 'testDropRule').mockResolvedValue({
      matched: true,
      error: null,
    });
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({
      id: 6,
      name: 'Drop Sshd Low Severity',
      source_pattern: null,
      app_pattern: 'sshd',
      message_pattern: '*',
      is_regex: false,
      is_enabled: true,
      severity_threshold: 6,
      dropped_count: 0,
      created_at: '2026-09-18T10:25:00Z',
    });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Rule'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText(/e\.g\. CRON Chatter/i);
    fireEvent.change(nameInput, { target: { value: 'Drop Sshd Low Severity' } });

    // Select severity threshold: Info (6) and less severe
    const sevSelect = screen.getByLabelText(/Severity Range/i);
    expect(sevSelect).toBeInTheDocument();
    fireEvent.change(sevSelect, { target: { value: '6' } });

    // Select custom app: sshd
    const appSelect = screen.getByLabelText(/Application or Container/i);
    fireEvent.change(appSelect, { target: { value: '__custom__' } });
    const customAppInput = screen.getByPlaceholderText('e.g. dnsmasq, smartbulb*, traefik');
    fireEvent.change(customAppInput, { target: { value: 'sshd' } });

    // Fill sample message and test
    const sampleMsg = screen.getByPlaceholderText('Paste a representative log line here...');
    fireEvent.change(sampleMsg, { target: { value: 'Disconnected from authenticating user' } });

    const sampleSevSelect = screen.getByLabelText(/Sample Severity/i);
    fireEvent.change(sampleSevSelect, { target: { value: '7' } });

    const testBtn = screen.getByText('Dry-Run Test');
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalledWith(expect.objectContaining({
        app_pattern: 'sshd',
        severity_threshold: 6,
        sample_severity: 7,
      }));
      expect(screen.getByText(/Rule matched/i)).toBeInTheDocument();
    });

    // Submit rule
    const saveBtn = screen.getByRole('button', { name: 'Create Rule' });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(expect.objectContaining({
        name: 'Drop Sshd Low Severity',
        message_pattern: '*',
        is_regex: false,
        source_pattern: undefined,
        app_pattern: 'sshd',
        severity_threshold: 6,
        is_enabled: true,
      }));
    });
  });

  it('displays Quick Drop Presets and installs a preset', async () => {
    const mockPresets = [
      {
        id: 'docker_healthcheck',
        name: 'Docker Healthcheck Noise',
        description: 'Filters repetitive container health checks.',
        source_pattern: null,
        app_pattern: null,
        message_pattern: 'healthcheck|health_status',
        is_regex: true,
        severity_threshold: null,
        is_custom: false,
      },
      {
        id: 'custom_preset',
        name: 'Custom Noise Filter',
        description: 'Custom community preset.',
        source_pattern: null,
        app_pattern: null,
        message_pattern: 'ping_test',
        is_regex: false,
        severity_threshold: 7,
        is_custom: true,
      },
    ];

    vi.spyOn(dropRulesApi, 'fetchDropPresets').mockResolvedValue(mockPresets);
    const installSpy = vi.spyOn(dropRulesApi, 'installDropPreset').mockResolvedValue({
      id: 3,
      source_pattern: null,
      app_pattern: null,
      message_pattern: 'healthcheck|health_status',
      is_regex: true,
      is_enabled: true,
      severity_threshold: null,
      dropped_count: 0,
      created_at: '2026-09-18T11:00:00Z',
    });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Presets/i })).toBeInTheDocument();
    });

    // Modal is initially closed
    expect(screen.queryByText('Drop Presets Catalog')).not.toBeInTheDocument();

    // Click Presets button to open modal
    fireEvent.click(screen.getByRole('button', { name: /Presets/i }));

    // Modal items visible
    expect(screen.getByText('Drop Presets Catalog')).toBeInTheDocument();
    expect(screen.getByText('Common Noise Filter Presets')).toBeInTheDocument();
    expect(screen.getByText('Docker Healthcheck Noise')).toBeInTheDocument();
    expect(screen.getByText('Custom Noise Filter')).toBeInTheDocument();
    expect(screen.getByText('Custom')).toBeInTheDocument();

    // Click Install on docker_healthcheck
    const installBtns = screen.getAllByRole('button', { name: /Install Rule/i });
    fireEvent.click(installBtns[0]);

    await waitFor(() => {
      expect(installSpy).toHaveBeenCalledWith('docker_healthcheck');
      expect(screen.getByText(/Drop preset "Docker Healthcheck Noise" installed\./i)).toBeInTheDocument();
    });
  });

  it('exports all drop rules when Export All button is clicked', async () => {
    const exportSpy = vi.spyOn(dropRulesApi, 'exportAllDropRules').mockResolvedValue(
      new Blob([JSON.stringify({ version: '1', drop_rules: mockRules })], { type: 'application/json' })
    );

    const restoreDownloads = stubDownloads();
    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Export All/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Export All/i }));
    expect(exportSpy).toHaveBeenCalled();
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:export'));
    expect(URL.createObjectURL).toHaveBeenCalledWith(await exportSpy.mock.results[0].value);
    restoreDownloads();
  });

  it('exports a single drop rule when row Export button is clicked', async () => {
    const exportSpy = vi.spyOn(dropRulesApi, 'exportSingleDropRule').mockResolvedValue(
      new Blob([JSON.stringify({ version: '1', drop_rule: mockRules[0] })], { type: 'application/json' })
    );

    const restoreDownloads = stubDownloads();
    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByLabelText('Export rule 1')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('Export rule 1'));
    expect(exportSpy).toHaveBeenCalledWith(1);
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:export'));
    restoreDownloads();
  });

  it('imports drop rules from a JSON file and displays feedback', async () => {
    const importSpy = vi.spyOn(dropRulesApi, 'importDropRules').mockResolvedValue({
      imported: 1,
      skipped: 1,
      errors: [],
    });

    const { container } = render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Import/i })).toBeInTheDocument();
    });

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeInTheDocument();

    const file = new File([JSON.stringify({ drop_rules: [] })], 'rules.json', { type: 'application/json' });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(importSpy).toHaveBeenCalled();
      expect(screen.getByText(/Imported 1 rule \(1 skipped\)\./i)).toBeInTheDocument();
    });
  });

  it('renders drop rules as responsive card list without table on mobile viewports', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query.includes('(max-width: 767px)'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('Ingestion Drop Rules')).toBeInTheDocument();
      expect(screen.getByText('Drop DHCPACK')).toBeInTheDocument();
      expect(screen.getByText('Drop Probe Requests')).toBeInTheDocument();
    });

    // Consistent responsive card layout: table is never rendered
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    // Card elements are rendered with matching rule test IDs
    expect(screen.getByTestId('drop-rule-row-1')).toBeInTheDocument();
    expect(screen.getByTestId('drop-rule-row-2')).toBeInTheDocument();
  });

  it('sends drop rule reorder requests one at a time and ends on the last move', async () => {
    const rules: DropRule[] = [1, 2, 3].map((id) => ({ ...mockRules[0], id, name: `Rule ${id}` }));
    vi.spyOn(dropRulesApi, 'fetchDropRules').mockResolvedValue(rules);
    const resolvers: Array<(r: DropRule[]) => void> = [];
    const reorderSpy = vi.spyOn(dropRulesApi, 'reorderDropRules').mockImplementation(
      () => new Promise<DropRule[]>((resolve) => resolvers.push(resolve))
    );
    const order = () =>
      screen.getAllByLabelText(/Move rule \d+ up/).map((el) => Number(el.getAttribute('aria-label')!.match(/\d+/)![0]));

    render(<DropRulesCard />);
    await waitFor(() => expect(screen.getByText('Rule 1')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText('Move rule 1 down'));
    fireEvent.click(screen.getByLabelText('Move rule 1 down'));
    expect(order()).toEqual([2, 3, 1]);
    expect(reorderSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolvers[0]([rules[1], rules[0], rules[2]]);
    });
    await waitFor(() => expect(reorderSpy).toHaveBeenCalledTimes(2));
    expect(reorderSpy).toHaveBeenLastCalledWith([2, 3, 1]);
    expect(order()).toEqual([2, 3, 1]);
  });

  it('clears the dragged row when a drag ends outside a row', async () => {
    render(<DropRulesCard />);
    await waitFor(() => expect(screen.getByTestId('drop-rule-row-1')).toBeInTheDocument());
    const row = screen.getByTestId('drop-rule-row-1');
    fireEvent.dragStart(row, { dataTransfer: { setData: vi.fn(), effectAllowed: '' } });
    expect(row.className).toContain('opacity-40');
    fireEvent.dragEnd(row);
    expect(row.className).not.toContain('opacity-40');
  });

  it('moves a dragged rule to the row it is dropped on, and a drop on itself sends nothing', async () => {
    const rules: DropRule[] = [1, 2, 3].map((id) => ({ ...mockRules[0], id, name: `Rule ${id}` }));
    vi.spyOn(dropRulesApi, 'fetchDropRules').mockResolvedValue(rules);
    const reorderSpy = vi
      .spyOn(dropRulesApi, 'reorderDropRules')
      .mockImplementation(async (ids: number[]) => ids.map((id) => rules.find((r) => r.id === id)!));
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };

    render(<DropRulesCard />);
    await waitFor(() => expect(screen.getByTestId('drop-rule-row-3')).toBeInTheDocument());

    fireEvent.dragStart(screen.getByTestId('drop-rule-row-1'), { dataTransfer });
    fireEvent.drop(screen.getByTestId('drop-rule-row-1'), { dataTransfer });
    expect(reorderSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('drop-rule-row-1').className).not.toContain('opacity-40');

    fireEvent.dragStart(screen.getByTestId('drop-rule-row-1'), { dataTransfer });
    fireEvent.dragOver(screen.getByTestId('drop-rule-row-3'), { dataTransfer });
    fireEvent.drop(screen.getByTestId('drop-rule-row-3'), { dataTransfer });
    await waitFor(() => expect(reorderSpy).toHaveBeenCalledWith([2, 3, 1]));
    expect(screen.getByTestId('drop-rule-row-1').className).not.toContain('opacity-40');
  });

  it('reports import errors from the server and a file that is not JSON', async () => {
    const importSpy = vi
      .spyOn(dropRulesApi, 'importDropRules')
      .mockResolvedValue({ imported: 0, skipped: 1, errors: ['Item #2: bad pattern'] });
    const { container } = render(<DropRulesCard />);
    await waitFor(() => expect(screen.getByRole('button', { name: /Import/i })).toBeInTheDocument());
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(fileInput, { target: { files: [new File(['{}'], 'rules.json')] } });
    await waitFor(() =>
      expect(screen.getByText('Imported 0, skipped 1. Errors: Item #2: bad pattern')).toBeInTheDocument()
    );

    fireEvent.change(fileInput, { target: { files: [new File(['not json'], 'rules.json')] } });
    await waitFor(() => expect(screen.getByText(/not valid JSON/)).toBeInTheDocument());
    expect(importSpy).toHaveBeenCalledTimes(1);
  });
});


describe('DropRulesCard pattern display', () => {
  const base = {
    source_pattern: null,
    app_pattern: null,
    is_regex: false,
    is_enabled: true,
    severity_threshold: null,
    dropped_count: 0,
    created_at: '2026-09-18T10:00:00Z',
  };

  it('shows the message pattern with a title, and Any when empty or *', async () => {
    vi.spyOn(dropRulesApi, 'fetchDropRules').mockResolvedValue([
      { ...base, id: 1, name: 'Has pattern', message_pattern: 'DHCPACK from 10.0.0.5' },
      { ...base, id: 2, name: 'Star pattern', message_pattern: '*' },
      { ...base, id: 3, name: 'Empty pattern', message_pattern: '' },
    ] as DropRule[]);
    render(<DropRulesCard />);
    await waitFor(() => expect(screen.getByText('Has pattern')).toBeInTheDocument());

    const pattern = screen.getByText('DHCPACK from 10.0.0.5');
    expect(pattern).toHaveAttribute('title', 'DHCPACK from 10.0.0.5');
    expect(pattern.className).toContain('font-mono');
    expect(screen.getAllByText('Pattern:')).toHaveLength(3);
    expect(screen.queryByText('*')).toBeNull();
    // Host, App and Pattern all read "Any" on the two rules without a pattern (plus Severity).
    expect(screen.getAllByText('Any').length).toBeGreaterThanOrEqual(6);
  });
});
