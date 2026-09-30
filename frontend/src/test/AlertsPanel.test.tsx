import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AlertsPanel, pathToAlertSubTab, alertSubTabToPath } from '../components/alerts/AlertsPanel.tsx';
import * as alertsApi from '../api/alerts.ts';
import * as notificationsApi from '../api/notifications.ts';
import * as dropRulesApi from '../api/dropRules.ts';

const mockRules = [
  {
    id: 1,
    name: 'SSH Brute-Force Detection',
    rule_type: 'threshold',
    channel_id: 1,
    filter_app: 'sshd',
    filter_severity: null,
    match_pattern: 'Failed password',
    threshold_count: 5,
    window_seconds: 60,
    cooldown_seconds: 300,
    ai_enrichment: true,
    is_enabled: true,
    trigger_count: 2,
    last_triggered_at: '2026-09-18T10:00:00Z',
    suppress_until: null,
    created_at: '2026-09-18T09:00:00Z',
  },
];

const mockPresets = [
  {
    id: 'ssh_bruteforce',
    name: 'SSH Brute-Force Detection',
    description: 'Detects repeated failed SSH authentication attempts from offending IP addresses.',
    rule_type: 'threshold',
    filter_app: 'sshd',
    filter_severity: null,
    match_pattern: 'Failed password',
    threshold_count: 5,
    window_seconds: 60,
    cooldown_seconds: 300,
    ai_enrichment: true,
    is_custom: false,
  },
  {
    id: 'oom_killer',
    name: 'Kernel Out-Of-Memory (OOM) Kill',
    description: 'Detects Linux kernel out-of-memory killer invocations.',
    rule_type: 'pattern',
    filter_app: null,
    filter_severity: null,
    match_pattern: 'Out of memory: Kill process',
    threshold_count: 1,
    window_seconds: 60,
    cooldown_seconds: 300,
    ai_enrichment: true,
    is_custom: true,
  },
];

const mockHistory = {
  items: [
    {
      id: 10,
      rule_id: 1,
      rule_name: 'SSH Brute-Force Detection',
      channel_id: 1,
      trigger_count: 5,
      sample_log: 'Failed password for root from 192.168.1.100 port 22',
      incident_summary: 'Brute-force attack from host 192.168.1.100 against root.',
      ai_enrichment: true,
      triggered_at: '2026-09-18T10:00:00Z',
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
};

const mockChannels = [
  {
    id: 1,
    name: 'Discord Ops',
    url: 'discord://webhook/********',
    is_enabled: true,
    created_at: '2026-09-18T08:00:00Z',
    updated_at: '2026-09-18T08:00:00Z',
  },
];

const mockDropRules = [
  {
    id: 1,
    source_pattern: '192.168.1.*',
    app_pattern: null,
    message_pattern: 'noise',
    is_regex: false,
    is_enabled: true,
    dropped_count: 15,
    created_at: '2026-09-18T10:00:00Z',
  },
  {
    id: 2,
    source_pattern: null,
    app_pattern: null,
    message_pattern: 'debug test',
    is_regex: false,
    is_enabled: false,
    dropped_count: 0,
    created_at: '2026-09-18T10:05:00Z',
  },
];

describe('AlertsPanel Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.history.pushState(null, '', '/rules');
    vi.spyOn(alertsApi, 'fetchAlertRules').mockResolvedValue(mockRules);
    vi.spyOn(alertsApi, 'fetchAlertPresets').mockResolvedValue(mockPresets);
    vi.spyOn(alertsApi, 'fetchAlertHistory').mockResolvedValue(mockHistory);
    vi.spyOn(notificationsApi, 'fetchNotificationChannels').mockResolvedValue(mockChannels);
    vi.spyOn(dropRulesApi, 'fetchDropRules').mockResolvedValue(mockDropRules);
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({ active: false, until: null });
  });

  it('renders Rules & History heading, subtitle with drop rules, and enabled drop rules tab count', async () => {
    render(<AlertsPanel />);

    expect(screen.getByRole('heading', { level: 2, name: /Rules & History/i })).toBeInTheDocument();
    expect(
      screen.getByText(/Configure real-time threshold and pattern alert rules, manage ingestion drop rules/i)
    ).toBeInTheDocument();

    // Verify top refresh button and Total active are removed
    expect(screen.queryByTitle('Refresh rules and status')).toBeNull();
    expect(screen.queryByText(/Total active:/i)).toBeNull();

    await waitFor(() => {
      // 1 enabled alert rule of 1 total in Option 1 split badge format
      expect(screen.getByRole('button', { name: /Alert Rules.*1\s*\/\s*1/i })).toBeInTheDocument();
      // 1 enabled drop rule out of 2 in Option 1 split badge format
      expect(screen.getByRole('button', { name: /Drop Rules.*1\s*\/\s*2/i })).toBeInTheDocument();
    });

    // On alert rules tab, New Alert Rule is visible
    expect(screen.getByRole('button', { name: /New Alert Rule/i })).toBeInTheDocument();

    // Switch to Drop Rules tab: New Alert Rule is hidden, New Rule for drop rules is visible
    const dropRulesTab = screen.getByRole('button', { name: /Drop Rules/i });
    fireEvent.click(dropRulesTab);

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /New Alert Rule/i })).toBeNull();
      expect(screen.getByRole('button', { name: /New Rule/i })).toBeInTheDocument();
    });
  });

  it('maps pathname to corresponding AlertViewTab and vice versa', () => {
    expect(pathToAlertSubTab('/rules')).toBe('rules');
    expect(pathToAlertSubTab('/rules/rules')).toBe('rules');
    expect(pathToAlertSubTab('/rules/presets')).toBe('rules');
    expect(pathToAlertSubTab('/rules/quick-rules')).toBe('rules');
    expect(pathToAlertSubTab('/rules/history')).toBe('history');
    expect(pathToAlertSubTab('/rules/drop-rules')).toBe('drop-rules');
    expect(pathToAlertSubTab('/rules/drop')).toBe('drop-rules');

    expect(alertSubTabToPath('rules')).toBe('/rules/rules');
    expect(alertSubTabToPath('history')).toBe('/rules/history');
    expect(alertSubTabToPath('drop-rules')).toBe('/rules/drop-rules');
  });

  it('renders alert rules and switches between tabs, and opens Presets modal', async () => {
    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
      expect(screen.getByText('AI Enriched')).toBeInTheDocument();
      expect(screen.getByText('Fired 2x')).toBeInTheDocument();
    });

    // Open Alert Presets modal
    const presetsBtn = screen.getByRole('button', { name: /^Presets/i });
    fireEvent.click(presetsBtn);

    await waitFor(() => {
      expect(screen.getByText('Alert Presets Catalog')).toBeInTheDocument();
      expect(screen.getByText('Kernel Out-Of-Memory (OOM) Kill')).toBeInTheDocument();
      expect(screen.getByText('Pre-tuned Monitoring Rules')).toBeInTheDocument();
    });

    // Close Presets modal
    const closeDialogBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeDialogBtn);

    await waitFor(() => {
      expect(screen.queryByText('Alert Presets Catalog')).not.toBeInTheDocument();
    });

    // Switch to History tab
    const historyTab = screen.getByRole('button', { name: /^History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 3, name: /^History/i })).toBeInTheDocument();
      expect(screen.getByText('AI Alert')).toBeInTheDocument();
    });

    // Expand row to view diagnosis summary
    const row = screen.getByText('SSH Brute-Force Detection');
    fireEvent.click(row);

    await waitFor(() => {
      expect(screen.getAllByText('Brute-force attack from host 192.168.1.100 against root.').length).toBeGreaterThan(0);
    });

    // Switch to Drop Rules tab
    const dropRulesTab = screen.getByRole('button', { name: /Drop Rules/i });
    fireEvent.click(dropRulesTab);

    await waitFor(() => {
      expect(screen.getByText('Ingestion Drop Rules')).toBeInTheDocument();
    });
  });

  it('allows toggling an alert rule status and dynamically updates the tab counter', async () => {
    const updateSpy = vi.spyOn(alertsApi, 'updateAlertRule').mockResolvedValue({
      ...mockRules[0],
      is_enabled: false,
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Alert Rules.*1\s*\/\s*1/i })).toBeInTheDocument();
    });

    const toggleBtn = screen.getByTitle('Click to disable');
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(1, { is_enabled: false });
      expect(screen.getByRole('button', { name: /Alert Rules.*0\s*\/\s*1/i })).toBeInTheDocument();
    });
  });

  it('opens create modal and submits a new alert rule', async () => {
    const createSpy = vi.spyOn(alertsApi, 'createAlertRule').mockResolvedValue({
      id: 2,
      name: 'Nginx 502 Bad Gateway',
      rule_type: 'threshold',
      channel_id: 1,
      filter_app: 'nginx',
      filter_severity: 3,
      match_pattern: '502 Bad Gateway',
      threshold_count: 10,
      window_seconds: 60,
      cooldown_seconds: 300,
      ai_enrichment: true,
      is_enabled: true,
      trigger_count: 0,
      created_at: '2026-09-18T11:00:00Z',
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    const newBtn = screen.getByRole('button', { name: /New Alert Rule/i });
    fireEvent.click(newBtn);

    expect(screen.getByRole('heading', { name: 'Create New Alert Rule' })).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText('e.g. Critical Auth Failure Spike');
    fireEvent.change(nameInput, { target: { value: 'Nginx 502 Bad Gateway' } });

    const saveBtn = screen.getByRole('button', { name: 'Create Rule' });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalled();
      expect(screen.getByText('Nginx 502 Bad Gateway')).toBeInTheDocument();
    });
  });

  it('installs a 1-click quick rule preset', async () => {
    const installSpy = vi.spyOn(alertsApi, 'installAlertPreset').mockResolvedValue({
      id: 3,
      name: 'Kernel Out-Of-Memory (OOM) Kill',
      rule_type: 'pattern',
      channel_id: null,
      filter_app: null,
      filter_severity: null,
      match_pattern: 'Out of memory: Kill process',
      threshold_count: 1,
      window_seconds: 60,
      cooldown_seconds: 300,
      ai_enrichment: true,
      is_enabled: true,
      trigger_count: 0,
      created_at: '2026-09-18T12:00:00Z',
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    // Open Presets modal
    const presetsBtn = screen.getByRole('button', { name: /^Presets/i });
    fireEvent.click(presetsBtn);

    await waitFor(() => {
      expect(screen.getByText('Kernel Out-Of-Memory (OOM) Kill')).toBeInTheDocument();
      expect(screen.getByText('Custom')).toBeInTheDocument();
    });

    const installBtns = screen.getAllByRole('button', { name: /Install Rule/i });
    fireEvent.click(installBtns[0]);

    await waitFor(() => {
      expect(installSpy).toHaveBeenCalled();
    });
  });

  it('renders failed status badge for failed AI analysis and shows Test Rule button', async () => {
    vi.spyOn(alertsApi, 'fetchAlertHistory').mockResolvedValue({
      items: [
        {
          id: 11,
          rule_id: 1,
          rule_name: 'SSH Brute-Force Detection',
          channel_id: 1,
          trigger_count: 5,
          sample_log: 'Failed password for root',
          incident_summary: 'AI analysis failed: Gemini API error 504 DEADLINE_EXCEEDED',
          ai_enrichment: true,
          triggered_at: '2026-09-18T10:05:00Z',
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    // Verify Test Rule button
    const testBtn = screen.getByTitle('Test Rule');
    expect(testBtn).toBeInTheDocument();

    // Switch to History tab
    const historyTab = screen.getByRole('button', { name: /^History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('AI Alert')).toBeInTheDocument();
      expect(screen.getByText(/AI analysis failed/i)).toBeInTheDocument();
    });

    const viewBtn = screen.getByRole('button', { name: 'View' });
    fireEvent.click(viewBtn);

    await waitFor(() => {
      expect(screen.getByText('Failed')).toBeInTheDocument();
      expect(screen.getByText('Failure Details')).toBeInTheDocument();
    });
  });

  it('renders Complete badge when AI diagnosis describes a system failure without AI engine error', async () => {
    vi.spyOn(alertsApi, 'fetchAlertHistory').mockResolvedValue({
      items: [
        {
          id: 12,
          rule_id: 1,
          rule_name: 'Kernel Hardware Watchdog',
          channel_id: 1,
          trigger_count: 1,
          sample_log: 'Critical hardware watchdog fired',
          incident_summary: 'The system experienced a critical emergency failure triggered by a hardware watchdog timer expiration. Root Cause: Kernel deadlock. Remediation: Check hardware health.',
          ai_enrichment: true,
          ai_model: 'gemini-2.5-flash',
          triggered_at: '2026-09-18T10:10:00Z',
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    render(<AlertsPanel />);

    const historyTab = screen.getByRole('button', { name: /^History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('Kernel Hardware Watchdog')).toBeInTheDocument();
      expect(screen.getByText('AI Alert')).toBeInTheDocument();
      expect(screen.queryByText('Failed')).not.toBeInTheDocument();
    });

    // Click View button to expand details
    const viewBtn = screen.getByRole('button', { name: 'View' });
    fireEvent.click(viewBtn);

    await waitFor(() => {
      expect(screen.getByText('Model:')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
      expect(screen.getByText('AI Incident Diagnosis & Remediation')).toBeInTheDocument();
      expect(screen.getByText('Triggering Log Snippet')).toBeInTheDocument();
      expect(screen.getByTitle('Copy triggering log')).toBeInTheDocument();
    });
  });

  it('prompts confirmation modal when deleting a single incident history item', async () => {
    const deleteSpy = vi.spyOn(alertsApi, 'deleteAlertHistoryItem').mockResolvedValue({ success: true } as any);

    render(<AlertsPanel />);

    // Switch to History tab
    const historyTab = screen.getByRole('button', { name: /^History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    const deleteBtn = screen.getByTitle('Delete history record');
    fireEvent.click(deleteBtn);

    // Confirmation modal should appear
    expect(screen.getByRole('heading', { name: 'Delete Incident Record' })).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to delete this incident record/i)).toBeInTheDocument();

    // Confirm deletion
    const confirmBtn = screen.getByRole('button', { name: 'Delete Record' });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith(10);
      expect(screen.queryByRole('heading', { name: 'Delete Incident Record' })).toBeNull();
    });
  });

  it('opens test modal with FlaskConical icon and allows running test', async () => {
    const testSpy = vi.spyOn(alertsApi, 'testAlertRule').mockResolvedValue({
      matched: true,
      extracted_ip: '192.168.1.100',
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    const testBtn = screen.getByTitle('Test Rule');
    fireEvent.click(testBtn);

    expect(screen.getByRole('heading', { name: /Dry-Run Test:/i })).toBeInTheDocument();

    const runBtn = screen.getByRole('button', { name: 'Run Test' });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalled();
      expect(screen.getByText('Pattern Matched Successfully')).toBeInTheDocument();
      expect(screen.getByText('192.168.1.100')).toBeInTheDocument();
    });
  });

  it('displays History table headers and opens modal overlay on incident row click', async () => {
    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    const historyTab = screen.getByRole('button', { name: /^History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('Target / Rule')).toBeInTheDocument();
      expect(screen.getByText('Summary')).toBeInTheDocument();
      expect(screen.queryByText('Sample Log')).toBeNull();
    });

    // Click on the incident row (matching rule name in Target / Rule column)
    const row = await screen.findByText('SSH Brute-Force Detection');
    fireEvent.click(row);

    // Verify modal overlay opens with Incident Alert Details title
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Incident Alert Details' })).toBeInTheDocument();
      expect(screen.getByText('Failed password for root from 192.168.1.100 port 22')).toBeInTheDocument();
      expect(screen.getByText('Triggering Log Snippet')).toBeInTheDocument();
    });

    // Close modal
    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Incident Alert Details' })).toBeNull();
    });
  });

  it('synchronizes alert subtab URLs and updates on browser popstate', async () => {
    const pushStateSpy = vi.spyOn(window.history, 'pushState');
    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
    });

    // Click Drop Rules
    const dropRulesTab = screen.getByRole('button', { name: /Drop Rules/i });
    fireEvent.click(dropRulesTab);
    expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/rules/drop-rules');

    // Click History
    const historyTab = screen.getByRole('button', { name: /^History/i });
    fireEvent.click(historyTab);
    expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/rules/history');

    // Click Alert Rules
    const rulesTab = screen.getByRole('button', { name: /Alert Rules/i });
    fireEvent.click(rulesTab);
    expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/rules/rules');
  });

  it('installs alert preset from presets modal with target channel', async () => {
    const installSpy = vi.spyOn(alertsApi, 'installAlertPreset').mockResolvedValue({
      id: 3,
      name: 'Kernel Out-Of-Memory (OOM) Kill',
      rule_type: 'pattern',
      channel_id: 1,
      filter_app: null,
      filter_severity: null,
      match_pattern: 'Out of memory: Kill process',
      threshold_count: 1,
      window_seconds: 60,
      cooldown_seconds: 300,
      ai_enrichment: true,
      is_enabled: true,
      trigger_count: 0,
      last_triggered_at: null,
      suppress_until: null,
      created_at: '2026-09-18T12:00:00Z',
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Presets/i })).toBeInTheDocument();
    });

    // Open Presets modal
    fireEvent.click(screen.getByRole('button', { name: /^Presets/i }));

    await waitFor(() => {
      expect(screen.getByText('Alert Presets Catalog')).toBeInTheDocument();
    });

    // Select target channel
    const channelSelect = screen.getByLabelText(/Target Channel:/i);
    fireEvent.change(channelSelect, { target: { value: '1' } });

    // Install OOM killer preset (second preset)
    const installBtns = screen.getAllByRole('button', { name: /Install Rule/i });
    fireEvent.click(installBtns[0]); // OOM is not already installed

    await waitFor(() => {
      expect(installSpy).toHaveBeenCalledWith('oom_killer', 1);
      expect(screen.getByText(/Alert preset "Kernel Out-Of-Memory \(OOM\) Kill" installed and active\./i)).toBeInTheDocument();
    });
  });

  it('displays updated description without QueueConsumer and matches Rule Guide link styling in modal', async () => {
    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Rules evaluated continuously against ingested log batches.')).toBeInTheDocument();
      expect(screen.queryByText(/by QueueConsumer/)).toBeNull();
    });

    // Open Create Rule modal
    const newRuleBtn = screen.getByRole('button', { name: /New Alert Rule/i });
    fireEvent.click(newRuleBtn);

    await waitFor(() => {
      const docLink = screen.getByTitle('Open Alert Rules Documentation');
      expect(docLink).toBeInTheDocument();
      expect(docLink.className).toContain('hover:underline');
      expect(docLink.className).toContain('inline-flex');
    });
  });

  it('highlights inactive or missing notification channel on rules without extra pill', async () => {
    const rulesWithBadChannel = [
      {
        id: 99,
        name: 'Orphan Channel Rule',
        rule_type: 'threshold',
        channel_id: 999, // channel 999 does not exist
        filter_app: 'nginx',
        filter_severity: null,
        match_pattern: 'error',
        threshold_count: 1,
        window_seconds: 60,
        cooldown_seconds: 300,
        ai_enrichment: false,
        is_enabled: true,
        trigger_count: 0,
        last_triggered_at: null,
        suppress_until: null,
        created_at: '2026-09-18T09:00:00Z',
      },
      {
        id: 100,
        name: 'All Channels Rule',
        rule_type: 'threshold',
        channel_id: null,
        filter_app: null,
        filter_severity: null,
        match_pattern: null,
        threshold_count: 1,
        window_seconds: 60,
        cooldown_seconds: 300,
        ai_enrichment: false,
        is_enabled: true,
        trigger_count: 0,
        last_triggered_at: null,
        suppress_until: null,
        created_at: '2026-09-18T09:00:00Z',
      },
    ];

    const channelsWithDisabled = [
      {
        id: 1,
        name: 'Discord Ops',
        url: 'discord://webhook/********',
        is_enabled: false,
        created_at: '2026-09-18T08:00:00Z',
        updated_at: '2026-09-18T08:00:00Z',
      },
    ];

    vi.spyOn(alertsApi, 'fetchAlertRules').mockResolvedValue(rulesWithBadChannel);
    vi.spyOn(notificationsApi, 'fetchNotificationChannels').mockResolvedValue(channelsWithDisabled);
    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Orphan Channel Rule')).toBeInTheDocument();
      expect(screen.queryByText('Channel Inactive')).toBeNull();
      expect(screen.getByText('(Channel not found or deleted)')).toBeInTheDocument();
      expect(screen.getByText('All Channels Rule')).toBeInTheDocument();
      expect(screen.getByText('(1 or more target channels disabled)')).toBeInTheDocument();
    });
  });

  it('exports all alert rules when Export All button is clicked', async () => {
    const exportSpy = vi.spyOn(alertsApi, 'exportAllAlertRules').mockResolvedValue(
      new Blob([JSON.stringify({ version: '1', alert_rules: [] })], { type: 'application/json' })
    );

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Export All/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Export All/i }));
    expect(exportSpy).toHaveBeenCalled();
  });

  it('exports a single alert rule when row Export button is clicked', async () => {
    const exportSpy = vi.spyOn(alertsApi, 'exportSingleAlertRule').mockResolvedValue(
      new Blob([JSON.stringify({ version: '1', alert_rule: {} })], { type: 'application/json' })
    );

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByLabelText('Export rule SSH Brute-Force Detection')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('Export rule SSH Brute-Force Detection'));
    expect(exportSpy).toHaveBeenCalledWith(1);
  });

  it('imports alert rules from a JSON file and displays feedback', async () => {
    const importSpy = vi.spyOn(alertsApi, 'importAlertRules').mockResolvedValue({
      imported: 2,
      skipped: 0,
      errors: [],
    });

    const { container } = render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Import/i })).toBeInTheDocument();
    });

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeInTheDocument();

    const file = new File([JSON.stringify({ alert_rules: [] })], 'rules.json', { type: 'application/json' });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(importSpy).toHaveBeenCalled();
      expect(screen.getByText(/Imported 2 rules \(0 skipped\)\./i)).toBeInTheDocument();
    });
  });

  it('renders combined History table with on-demand and alert badges and opens details', async () => {
    vi.spyOn(alertsApi, 'fetchAlertHistory').mockResolvedValue({
      items: [
        {
          id: 1,
          rule_id: null,
          rule_name: 'On-Demand Analysis',
          channel_id: null,
          trigger_count: 3,
          sample_log: null,
          incident_summary: '## Summary\nContainer memory leak detected.\n\n## Root Cause\nOOM.\n\n## Actionable Remediation\nRestart container.',
          ai_enrichment: true,
          ai_model: 'gemini-3.7-flash',
          ai_audit_id: 101,
          triggered_at: '2026-09-25T14:30:00Z',
          source_alias: 'docker',
          app_name: 'web-api',
          tokens_used: 1250,
          tokens_in: 900,
          tokens_out: 350,
        },
        {
          id: 2,
          rule_id: 1,
          rule_name: 'SSH Brute-Force Detection',
          channel_id: 1,
          trigger_count: 10,
          sample_log: 'Failed password for root',
          incident_summary: 'Brute-force password guessing attack.',
          ai_enrichment: true,
          ai_model: 'gemini-3.7-flash',
          ai_audit_id: 102,
          triggered_at: '2026-09-25T14:00:00Z',
          tokens_used: 800,
        },
        {
          id: 3,
          rule_id: 2,
          rule_name: 'Nginx Error Spike',
          channel_id: null,
          trigger_count: 50,
          sample_log: '502 Bad Gateway',
          incident_summary: null,
          ai_enrichment: false,
          triggered_at: '2026-09-25T13:00:00Z',
        },
      ],
      total: 3,
      limit: 50,
      offset: 0,
    });

    render(<AlertsPanel />);

    // Switch to History tab
    const historyTab = screen.getByRole('button', { name: /^History/i });
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByText('On-Demand')).toBeInTheDocument();
      expect(screen.getByText('AI Alert')).toBeInTheDocument();
      expect(screen.getByText('Alert')).toBeInTheDocument();
      expect(screen.getByText('docker • web-api')).toBeInTheDocument();
      expect(screen.getByText('SSH Brute-Force Detection')).toBeInTheDocument();
      expect(screen.getByText('Nginx Error Spike')).toBeInTheDocument();
      // Clean summary should strip ## Summary
      expect(screen.getByText('Container memory leak detected.')).toBeInTheDocument();
    });

    // Ensure no cross-link banner to Storage is displayed
    expect(screen.queryByText(/Looking for AI token usage audits and raw prompt histories/i)).not.toBeInTheDocument();

    // Click on On-Demand row to open Historical AI Analysis modal
    const onDemandRow = screen.getByText('docker • web-api');
    fireEvent.click(onDemandRow);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Historical AI Analysis' })).toBeInTheDocument();
      expect(screen.getByText(/1,250/)).toBeInTheDocument();
      expect(screen.getByText(/900 in/)).toBeInTheDocument();
      expect(screen.getByText(/350 out/)).toBeInTheDocument();
    });
  });

  it('correctly maps maintenance sub-tab paths', () => {
    expect(pathToAlertSubTab('/rules/maintenance')).toBe('maintenance');
    expect(alertSubTabToPath('maintenance')).toBe('/rules/maintenance');
  });

  it('renders active maintenance banner on the main rules tab and switches to maintenance sub-tab via Manage Maintenance button', async () => {
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({
      active: true,
      until: '2026-09-26T15:00:00Z',
      schedule_name: null,
      on_demand_until: '2026-09-26T15:00:00Z',
      schedules: [],
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Maintenance Window Active')).toBeInTheDocument();
      expect(screen.getByText('On-Demand')).toBeInTheDocument();
      expect(screen.getByText(/Alert notifications are silenced/i)).toBeInTheDocument();
    });

    // Manage button switches to maintenance sub-tab
    const manageBtn = screen.getByRole('button', { name: 'Manage Maintenance' });
    fireEvent.click(manageBtn);

    await waitFor(() => {
      expect(screen.getByText('On-Demand Maintenance')).toBeInTheDocument();
      expect(screen.getByText('Scheduled Maintenance Windows')).toBeInTheDocument();
    });
  });

  it('clears on-demand maintenance directly from the main rules tab banner', async () => {
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({
      active: true,
      until: '2026-09-26T15:00:00Z',
      schedule_name: null,
      on_demand_until: '2026-09-26T15:00:00Z',
      schedules: [],
    });
    const setMaintSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue({
      active: false,
      until: null,
      schedules: [],
    });

    render(<AlertsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Maintenance Window Active')).toBeInTheDocument();
    });

    const clearBtn = screen.getByRole('button', { name: 'Clear' });
    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(setMaintSpy).toHaveBeenCalledWith(null);
      expect(screen.queryByText('Maintenance Window Active')).toBeNull();
    });
  });

  it('switches to maintenance sub-tab and renders on-demand controls and presets', async () => {
    const setMaintSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue({
      active: true,
      until: '2026-09-26T12:00:00Z',
      on_demand_until: '2026-09-26T12:00:00Z',
      schedules: [],
    });

    render(<AlertsPanel />);

    // Switch to maintenance sub-tab
    const maintTabBtn = screen.getByRole('button', { name: /Maintenance/i });
    fireEvent.click(maintTabBtn);

    await waitFor(() => {
      expect(screen.getByText('On-Demand Maintenance')).toBeInTheDocument();
      expect(screen.getByText('Scheduled Maintenance Windows')).toBeInTheDocument();
      expect(screen.getByText('On-Demand Inactive')).toBeInTheDocument();
    });

    // Click +1h preset
    const preset1hBtn = screen.getByRole('button', { name: '+1 Hour' });
    fireEvent.click(preset1hBtn);

    await waitFor(() => {
      expect(setMaintSpy).toHaveBeenCalled();
      expect(screen.getByText('On-Demand Active')).toBeInTheDocument();
    });
  });

  it('submits custom datetime for on-demand maintenance on the maintenance sub-tab', async () => {
    const setMaintSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue({
      active: true,
      until: '2026-09-26T18:00:00Z',
      on_demand_until: '2026-09-26T18:00:00Z',
      schedules: [],
    });

    render(<AlertsPanel />);

    const maintTabBtn = screen.getByRole('button', { name: /Maintenance/i });
    fireEvent.click(maintTabBtn);

    await waitFor(() => {
      expect(screen.getByText('On-Demand Maintenance')).toBeInTheDocument();
    });

    const dateInput = screen.getByLabelText('Maintenance window end time');
    fireEvent.change(dateInput, { target: { value: '2026-09-26T18:00' } });

    const setWindowBtn = screen.getByRole('button', { name: 'Set Window' });
    fireEvent.click(setWindowBtn);

    await waitFor(() => {
      expect(setMaintSpy).toHaveBeenCalled();
      expect(screen.getByText('On-Demand Active')).toBeInTheDocument();
    });
  });

  it('manages recurring maintenance schedules (add, toggle, delete)', async () => {
    const mockSched = {
      id: 'sched_1',
      name: 'Sunday System Backup',
      recurrence: 'weekly' as const,
      start_time: '02:00',
      duration_minutes: 60,
      day_of_week: 0,
      enabled: true,
      is_active: false,
    };

    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({
      active: false,
      until: null,
      schedules: [mockSched],
    });

    const updateSchedulesSpy = vi.spyOn(alertsApi, 'updateMaintenanceSchedules').mockResolvedValue({
      active: false,
      until: null,
      schedules: [{ ...mockSched, enabled: false }],
    });

    render(<AlertsPanel />);

    const maintTabBtn = screen.getByRole('button', { name: /Maintenance/i });
    fireEvent.click(maintTabBtn);

    await waitFor(() => {
      expect(screen.getByText('Sunday System Backup')).toBeInTheDocument();
      expect(screen.getByText('Weekly on Sunday at 02:00')).toBeInTheDocument();
      expect(screen.getByText('60 min')).toBeInTheDocument();
    });

    // Toggle enabled button
    const toggleBtn = screen.getByRole('button', { name: 'Enabled' });
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(updateSchedulesSpy).toHaveBeenCalledWith([
        expect.objectContaining({ id: 'sched_1', enabled: false }),
      ]);
    });

    // Add schedule modal
    const addBtn = screen.getByRole('button', { name: 'Add Schedule' });
    fireEvent.click(addBtn);

    expect(screen.getByRole('heading', { name: 'Add Maintenance Schedule' })).toBeInTheDocument();

    const nameInput = screen.getByLabelText('Schedule Name');
    fireEvent.change(nameInput, { target: { value: 'Daily Patching' } });

    const dailyBtn = screen.getByRole('button', { name: 'Daily' });
    fireEvent.click(dailyBtn);

    updateSchedulesSpy.mockResolvedValueOnce({
      active: false,
      until: null,
      schedules: [
        mockSched,
        {
          id: 'sched_2',
          name: 'Daily Patching',
          recurrence: 'daily',
          start_time: '02:00',
          duration_minutes: 60,
          enabled: true,
        },
      ],
    });

    const saveBtn = screen.getByRole('button', { name: 'Create Schedule' });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSchedulesSpy).toHaveBeenCalled();
    });

    // Delete schedule
    const deleteBtn = screen.getByLabelText('Delete Sunday System Backup');
    fireEvent.click(deleteBtn);

    expect(screen.getByRole('heading', { name: 'Delete Maintenance Schedule' })).toBeInTheDocument();

    updateSchedulesSpy.mockResolvedValueOnce({
      active: false,
      until: null,
      schedules: [],
    });

    const confirmDeleteBtn = screen.getByRole('button', { name: 'Delete Schedule' });
    fireEvent.click(confirmDeleteBtn);

    await waitFor(() => {
      expect(updateSchedulesSpy).toHaveBeenCalled();
    });
  });

  it('renders advisory banner when alert rules exist but no notification channels are configured', async () => {
    vi.spyOn(notificationsApi, 'fetchNotificationChannels').mockResolvedValue([]);
    const onNavigateToSettings = vi.fn();

    render(<AlertsPanel onNavigateToSettings={onNavigateToSettings} />);

    await waitFor(() => {
      expect(screen.getByText('No Notification Channels Configured')).toBeInTheDocument();
    });

    expect(
      screen.getByText(/Alert rules will record incidents in the History tab\. To receive push notifications, configure a notification channel in Settings\./i)
    ).toBeInTheDocument();

    const configureBtn = screen.getByRole('button', { name: /Configure in Settings/i });
    expect(configureBtn).toBeInTheDocument();

    fireEvent.click(configureBtn);
    expect(onNavigateToSettings).toHaveBeenCalled();
  });
});



