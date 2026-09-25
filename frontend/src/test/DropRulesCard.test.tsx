import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DropRulesCard } from '../components/settings/DropRulesCard.tsx';
import * as dropRulesApi from '../api/dropRules.ts';
import { DropRule } from '../types.ts';

describe('DropRulesCard Component', () => {
  const mockRules: DropRule[] = [
    {
      id: 1,
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
      expect(screen.getByText('DHCPACK')).toBeInTheDocument();
      expect(screen.getByText('probe request')).toBeInTheDocument();
      expect(screen.getByText('192.168.1.*')).toBeInTheDocument();
      expect(screen.getByText('dnsmasq')).toBeInTheDocument();
      expect(screen.getByText('Info and below')).toBeInTheDocument();
      expect(screen.getByText('Any severity')).toBeInTheDocument();
    });

    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
  });

  it('toggles rule enabled/disabled status', async () => {
    const updateSpy = vi.spyOn(dropRulesApi, 'updateDropRule').mockResolvedValue({
      ...mockRules[0],
      is_enabled: false,
    });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('DHCPACK')).toBeInTheDocument();
    });

    const activeBtn = screen.getByText('Active');
    fireEvent.click(activeBtn);

    expect(updateSpy).toHaveBeenCalledWith(1, { is_enabled: false });
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
      expect(screen.getByText('DHCPACK')).toBeInTheDocument();
    });

    // Click pencil edit icon
    const editBtn = screen.getByLabelText('Edit rule 1');
    fireEvent.click(editBtn);

    // Verify modal is in edit mode
    expect(screen.getByText('Edit Ingestion Drop Rule')).toBeInTheDocument();

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
        message_pattern: 'DHCPACK-UPDATED',
      }));
      expect(screen.getByText('Drop rule updated successfully.')).toBeInTheDocument();
    });
  });

  it('requires confirmation before deleting a drop rule and allows cancel', async () => {
    const deleteSpy = vi.spyOn(dropRulesApi, 'deleteDropRule').mockResolvedValue({ status: 'ok' });

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByText('DHCPACK')).toBeInTheDocument();
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
    expect(screen.getByText('DHCPACK')).toBeInTheDocument();

    // Open confirmation again and confirm
    fireEvent.click(delBtn);
    expect(screen.getByText('Delete this drop rule?')).toBeInTheDocument();

    const confirmDelBtn = screen.getByRole('button', { name: 'Delete Rule' });
    fireEvent.click(confirmDelBtn);

    expect(deleteSpy).toHaveBeenCalledWith(1);
    await waitFor(() => {
      expect(screen.queryByText('DHCPACK')).not.toBeInTheDocument();
    });
  });

  it('opens add rule modal, performs dry-run test, and saves rule', async () => {
    const testSpy = vi.spyOn(dropRulesApi, 'testDropRule').mockResolvedValue({
      matched: true,
      error: null,
    });
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({
      id: 3,
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
      expect(screen.getByText('DHCPACK')).toBeInTheDocument();
    });

    // Open Modal
    fireEvent.click(screen.getByText('New Rule'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // Fill form
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
      expect(createSpy).toHaveBeenCalledWith({
        message_pattern: 'healthcheck',
        is_regex: false,
        source_pattern: undefined,
        app_pattern: undefined,
        severity_threshold: undefined,
        is_enabled: true,
      });
      expect(screen.getByText('healthcheck')).toBeInTheDocument();
    });
  });

  it('allows selecting host/app from dropdown or entering custom wildcard', async () => {
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({
      id: 4,
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
      expect(screen.getByText('DHCPACK')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Rule'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

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
      expect(createSpy).toHaveBeenCalledWith({
        message_pattern: 'timeout',
        is_regex: false,
        source_pattern: 'router*',
        app_pattern: 'traefik',
        severity_threshold: undefined,
        is_enabled: true,
      });
    });
  });

  it('allows dropping all logs for an app with message pattern * or empty', async () => {
    const testSpy = vi.spyOn(dropRulesApi, 'testDropRule').mockResolvedValue({
      matched: true,
      error: null,
    });
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({
      id: 5,
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
      expect(screen.getByText('DHCPACK')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Rule'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

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
      expect(createSpy).toHaveBeenCalledWith({
        message_pattern: '*',
        is_regex: false,
        source_pattern: undefined,
        app_pattern: 'pvedaemon',
        severity_threshold: undefined,
        is_enabled: true,
      });
    });
  });

  it('creates a drop rule with severity threshold', async () => {
    const testSpy = vi.spyOn(dropRulesApi, 'testDropRule').mockResolvedValue({
      matched: true,
      error: null,
    });
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({
      id: 6,
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
      expect(screen.getByText('DHCPACK')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Rule'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // Select severity threshold: Info (6) and below
    const sevSelect = screen.getByLabelText(/Drop if severity is\.\.\./i);
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
      expect(createSpy).toHaveBeenCalledWith({
        message_pattern: '*',
        is_regex: false,
        source_pattern: undefined,
        app_pattern: 'sshd',
        severity_threshold: 6,
        is_enabled: true,
      });
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

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Export All/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Export All/i }));
    expect(exportSpy).toHaveBeenCalled();
  });

  it('exports a single drop rule when row Export button is clicked', async () => {
    const exportSpy = vi.spyOn(dropRulesApi, 'exportSingleDropRule').mockResolvedValue(
      new Blob([JSON.stringify({ version: '1', drop_rule: mockRules[0] })], { type: 'application/json' })
    );

    render(<DropRulesCard />);

    await waitFor(() => {
      expect(screen.getByLabelText('Export rule 1')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('Export rule 1'));
    expect(exportSpy).toHaveBeenCalledWith(1);
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
});

