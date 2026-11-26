import React from 'react';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AlertRuleModal } from '../components/alerts/AlertRuleModal.tsx';
import * as alertsApi from '../api/alerts.ts';
import { AlertRule, NotificationChannel } from '../types.ts';
import { chooseOption, optionLabels, shownValue } from './selectDropdownHelpers.ts';

const mockChannels: NotificationChannel[] = [
  {
    id: 1,
    name: 'Discord Ops',
    url: 'discord://webhook/********',
    is_enabled: true,
    created_at: '2026-09-18T08:00:00Z',
    updated_at: '2026-09-18T08:00:00Z',
  },
];

const mockRule: AlertRule = {
  id: 42,
  name: 'Database Timeout Spike',
  rule_type: 'threshold',
  channel_id: 1,
  filter_app: 'postgres',
  filter_severity: 3,
  match_pattern: 'canceling statement due to statement timeout',
  threshold_count: 5,
  window_seconds: 120,
  cooldown_seconds: 600,
  ai_enrichment: true,
  is_enabled: true,
  trigger_count: 0,
  last_triggered_at: null,
  suppress_until: null,
  created_at: '2026-09-18T09:00:00Z',
};

describe('AlertRuleModal Component', () => {
  const onClose = vi.fn();
  const onSuccess = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it('does not render dialog content when isOpen is false', () => {
    render(
      <AlertRuleModal
        isOpen={false}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={['postgres', 'nginx']}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    expect(screen.queryByRole('heading', { name: 'Create New Alert Rule' })).toBeNull();
  });

  it('opens with the cursor in Rule Name, as the drop rule dialog does', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={['postgres', 'nginx']}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    expect(document.activeElement).toBe(screen.getByLabelText('Rule Name'));
  });

  it('renders create modal and submits new alert rule', async () => {
    const createdRule: AlertRule = {
      ...mockRule,
      id: 99,
      name: 'Auth Failures',
    };
    const createSpy = vi.spyOn(alertsApi, 'createAlertRule').mockResolvedValue(createdRule);

    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={['postgres', 'nginx']}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    expect(screen.getByRole('heading', { name: 'Create New Alert Rule' })).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText('e.g. Critical Auth Failure Spike');
    fireEvent.change(nameInput, { target: { value: 'Auth Failures' } });

    const submitBtn = screen.getByRole('button', { name: 'Create Rule' });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Auth Failures',
          rule_type: 'threshold',
          channel_id: 1,
        })
      );
      expect(onSuccess).toHaveBeenCalledWith(createdRule);
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('renders edit modal pre-filled and submits updated alert rule', async () => {
    const updatedRule: AlertRule = {
      ...mockRule,
      name: 'Database Timeout Spike Updated',
    };
    const updateSpy = vi.spyOn(alertsApi, 'updateAlertRule').mockResolvedValue(updatedRule);

    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={mockRule}
        channels={mockChannels}
        availableApps={['postgres', 'nginx']}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    expect(screen.getByRole('heading', { name: 'Edit Alert Rule' })).toBeInTheDocument();

    const nameInput = screen.getByDisplayValue('Database Timeout Spike');
    fireEvent.change(nameInput, { target: { value: 'Database Timeout Spike Updated' } });

    const updateBtn = screen.getByRole('button', { name: 'Update Rule' });
    fireEvent.click(updateBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        42,
        expect.objectContaining({
          name: 'Database Timeout Spike Updated',
          filter_app: 'postgres',
        })
      );
      expect(onSuccess).toHaveBeenCalledWith(updatedRule);
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('shows the rule type, channel and severity it edits, and saves the ones chosen', async () => {
    const updateSpy = vi.spyOn(alertsApi, 'updateAlertRule').mockResolvedValue(mockRule);
    const channels = [...mockChannels, { ...mockChannels[0], id: 2, name: 'Email', is_enabled: false }];
    render(
      <AlertRuleModal isOpen={true} ruleToEdit={mockRule} channels={channels} availableApps={[]} onClose={onClose} onSuccess={onSuccess} />
    );
    const ruleType = screen.getByLabelText('Rule Type');
    const channel = screen.getByLabelText('Target Channel');
    const severity = screen.getByLabelText('Severity Filter (Optional)');
    expect(shownValue(ruleType)).toBe('Threshold (Sliding Window)');
    expect(shownValue(channel)).toBe('Discord Ops');
    expect(shownValue(severity)).toBe('Error (3) and more severe');
    expect(optionLabels(channel)).toEqual(['All Enabled Channels', 'Discord Ops', 'Email (Disabled)']);

    chooseOption(ruleType, 'Pattern (Immediate Match)');
    chooseOption(channel, 'Email (Disabled)');
    chooseOption(severity, 'Any Severity');
    fireEvent.click(screen.getByRole('button', { name: 'Update Rule' }));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        42,
        expect.objectContaining({ rule_type: 'pattern', channel_id: 2, filter_severity: null })
      );
    });
  });

  it('displays error if rule name is empty', async () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={[]}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const nameInput = screen.getByPlaceholderText('e.g. Critical Auth Failure Spike');
    fireEvent.change(nameInput, { target: { value: '   ' } });

    const submitBtn = screen.getByRole('button', { name: 'Create Rule' });
    fireEvent.click(submitBtn);

    expect(screen.getByText('Rule name is required.')).toBeInTheDocument();
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('disables AI enrichment checkbox and displays settings guidance when AI is not configured', async () => {
    const onNavigateToSettings = vi.fn();

    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={[]}
        isAiConfigured={false}
        onNavigateToSettings={onNavigateToSettings}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const enrichmentCheckbox = screen.getByRole('checkbox', {
      name: /AI Root-Cause Incident Enrichment/i,
    });
    expect(enrichmentCheckbox).toBeDisabled();
    expect(enrichmentCheckbox).not.toBeChecked();

    expect(
      screen.getByText(/AI enrichment requires an active AI provider/i)
    ).toBeInTheDocument();

    const configureBtn = screen.getByRole('button', { name: /Configure in Settings/i });
    fireEvent.click(configureBtn);

    expect(onClose).toHaveBeenCalled();
    expect(onNavigateToSettings).toHaveBeenCalled();
  });

  it('displays warning when editing rule with enrichment enabled while AI is unconfigured', async () => {
    const onNavigateToSettings = vi.fn();
    const enrichedRule: AlertRule = {
      ...mockRule,
      ai_enrichment: true,
    };

    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={enrichedRule}
        channels={mockChannels}
        availableApps={[]}
        isAiConfigured={false}
        onNavigateToSettings={onNavigateToSettings}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const enrichmentCheckbox = screen.getByRole('checkbox', {
      name: /AI Root-Cause Incident Enrichment/i,
    });
    // Checkbox is not disabled so user can uncheck it if desired
    expect(enrichmentCheckbox).not.toBeDisabled();
    expect(enrichmentCheckbox).toBeChecked();

    expect(
      screen.getByText(/AI provider is disabled or not configured/i)
    ).toBeInTheDocument();
  });

  it('closes immediately without warning when X button is clicked with no unsaved changes', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={mockRule}
        channels={mockChannels}
        availableApps={[]}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    expect(screen.queryByText('Unsaved Changes')).toBeNull();
    expect(onClose).toHaveBeenCalled();
  });

  it('warns about unsaved changes when user edits rule name and clicks X button', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={mockRule}
        channels={mockChannels}
        availableApps={[]}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const nameInput = screen.getByDisplayValue('Database Timeout Spike');
    fireEvent.change(nameInput, { target: { value: 'Renamed Rule' } });

    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    expect(
      screen.getByText('You have unsaved changes. Closing now will discard those edits.')
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('keeps editing when "Keep Editing" is clicked in unsaved changes confirmation', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={mockRule}
        channels={mockChannels}
        availableApps={[]}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const nameInput = screen.getByDisplayValue('Database Timeout Spike');
    fireEvent.change(nameInput, { target: { value: 'Renamed Rule' } });

    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();

    const keepEditingBtn = screen.getByRole('button', { name: 'Keep Editing' });
    fireEvent.click(keepEditingBtn);

    expect(screen.queryByText('Unsaved Changes')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue('Renamed Rule')).toBeInTheDocument();
  });

  it('discards edits and closes when "Discard Changes" is clicked in unsaved changes confirmation', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={mockRule}
        channels={mockChannels}
        availableApps={[]}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const nameInput = screen.getByDisplayValue('Database Timeout Spike');
    fireEvent.change(nameInput, { target: { value: 'Renamed Rule' } });

    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    const discardBtn = screen.getByRole('button', { name: 'Discard Changes' });
    fireEvent.click(discardBtn);

    expect(screen.queryByText('Unsaved Changes')).toBeNull();
    expect(onClose).toHaveBeenCalled();
  });

  it('warns about unsaved changes when user clicks Cancel button with edits', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={mockRule}
        channels={mockChannels}
        availableApps={[]}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

    const nameInput = screen.getByDisplayValue('Database Timeout Spike');
    fireEvent.change(nameInput, { target: { value: 'Renamed Rule' } });

    const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
    fireEvent.click(cancelBtn);

    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('lists all 8 severities plus Any in the max severity filter', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={[]}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );
    const texts = ['Emergency (0) only', 'Alert (1) and more severe', 'Critical (2) and more severe', 'Error (3) and more severe', 'Warning (4) and more severe', 'Notice (5) and more severe', 'Info (6) and more severe', 'Debug (7): every level'];
    expect(optionLabels(screen.getByLabelText('Severity Filter (Optional)'))).toEqual(['Any Severity', ...texts]);
  });
});

describe('AlertRuleModal severity wording', () => {
  it('labels the field and explains the direction', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={[]}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />
    );
    expect(screen.getByText('Severity Filter (Optional)')).toBeInTheDocument();
    expect(screen.queryByText(/Max Severity/)).toBeNull();
    expect(screen.getByText('Only logs at this level or more severe are checked.')).toBeInTheDocument();
  });
});

describe('AlertRuleModal prefill from a log', () => {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const message = 'upstream timed out (110: Connection timed out) while reading response header from upstream';

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    onClose.mockReset();
  });

  const renderPrefilled = (prefill: React.ComponentProps<typeof AlertRuleModal>['prefill']) =>
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={['postgres', 'nginx']}
        prefill={prefill}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );

  it('starts with the name and app filter, leaves severity and pattern empty, and saves them', async () => {
    const createSpy = vi.spyOn(alertsApi, 'createAlertRule').mockResolvedValue({ ...mockRule, id: 7 });
    renderPrefilled({ name: 'Alert on nginx', filterApps: ['nginx'], referenceMessage: message });

    expect(screen.getByPlaceholderText('e.g. Critical Auth Failure Spike')).toHaveValue('Alert on nginx');
    expect(screen.getByLabelText('Match Pattern')).toHaveValue('');

    fireEvent.click(screen.getByRole('button', { name: 'Create Rule' }));
    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Alert on nginx',
          filter_app: 'nginx',
          filter_severity: null,
          match_pattern: null,
        })
      )
    );
  });

  it('shows the whole log message in a box labelled From log', () => {
    renderPrefilled({ name: 'Alert on nginx', filterApps: ['nginx'], referenceMessage: message });
    const ref = screen.getByLabelText('From log') as HTMLTextAreaElement;
    expect(ref.tagName).toBe('TEXTAREA');
    expect(ref).toHaveValue(message);
    expect(screen.getByRole('button', { name: 'Use selection as pattern' })).toBeDisabled();
  });

  it('uses the selected part of the log line as the pattern, escaped when the rule is a regular expression', () => {
    const line = 'upstream timed out (110: Connection timed out) while reading [backend] 1.5s';
    renderPrefilled({ name: 'Alert on nginx', filterApps: ['nginx'], referenceMessage: line });
    const ref = screen.getByLabelText('From log') as HTMLTextAreaElement;
    const select = (part: string) => {
      const start = line.indexOf(part);
      ref.setSelectionRange(start, start + part.length);
      fireEvent.select(ref);
    };
    const pattern = screen.getByLabelText('Match Pattern');
    const use = screen.getByRole('button', { name: 'Use selection as pattern' });

    select(' (110: Connection timed out) ');
    fireEvent.click(use);
    expect(pattern).toHaveValue('(110: Connection timed out)');

    fireEvent.click(screen.getByLabelText('Match as a regular expression'));
    select('[backend] 1.5s');
    fireEvent.click(use);
    expect(pattern).toHaveValue('\\[backend\\] 1\\.5s');
    expect(new RegExp((pattern as HTMLInputElement).value).test(line)).toBe(true);
  });

  it('ticking the regex box after a selection escapes it, and the regex hint does the same', () => {
    const line = 'disk (sda) failed';
    renderPrefilled({ name: 'Alert on nginx', filterApps: ['nginx'], referenceMessage: line });
    const ref = screen.getByLabelText('From log') as HTMLTextAreaElement;
    ref.setSelectionRange(0, 10);
    fireEvent.select(ref);
    fireEvent.click(screen.getByRole('button', { name: 'Use selection as pattern' }));
    const pattern = screen.getByLabelText('Match Pattern');
    expect(pattern).toHaveValue('disk (sda)');
    // The plain text looks like a regex: taking the hint keeps it matching the same text
    fireEvent.click(screen.getByTestId('alert-rule-regex-hint').querySelector('button')!);
    expect(pattern).toHaveValue('disk \\(sda\\)');
    fireEvent.click(screen.getByLabelText('Match as a regular expression'));
    expect(pattern).toHaveValue('disk (sda)');
  });

  it('forgets the selection when the dialog opens again for another log', () => {
    const props = { isOpen: true, ruleToEdit: null, channels: mockChannels, availableApps: ['nginx'], onClose: vi.fn(), onSuccess: vi.fn() };
    const { rerender } = render(<AlertRuleModal {...props} prefill={{ name: 'A', referenceMessage: 'first line here' }} />);
    const ref = screen.getByLabelText('From log') as HTMLTextAreaElement;
    ref.setSelectionRange(0, 5);
    fireEvent.select(ref);
    expect(screen.getByRole('button', { name: 'Use selection as pattern' })).not.toBeDisabled();
    rerender(<AlertRuleModal {...props} isOpen={false} prefill={{ name: 'A', referenceMessage: 'first line here' }} />);
    rerender(<AlertRuleModal {...props} prefill={{ name: 'B', referenceMessage: 'second line' }} />);
    expect(screen.getByLabelText('From log')).toHaveValue('second line');
    expect(screen.getByRole('button', { name: 'Use selection as pattern' })).toBeDisabled();
  });

  it('follows a selection made by touch in the From log box (selectionchange only)', () => {
    renderPrefilled({ name: 'Alert on nginx', filterApps: ['nginx'], referenceMessage: 'disk sda failed' });
    const ref = screen.getByLabelText('From log') as HTMLTextAreaElement;
    // iOS starts a long-press selection with a mousedown and sends no mouseup, so React's
    // onSelect waits for a mouseup that never comes
    fireEvent.mouseDown(ref);
    ref.focus();
    ref.setSelectionRange(5, 8);
    act(() => {
      document.dispatchEvent(new Event('selectionchange'));
    });
    fireEvent.click(screen.getByRole('button', { name: 'Use selection as pattern' }));
    expect(screen.getByLabelText('Match Pattern')).toHaveValue('sda');
    // React keeps the mouse state for the whole module: release it for the tests after this one
    fireEvent.mouseUp(ref);
  });

  it('refuses a selection longer than a pattern can be', () => {
    const line = `start ${'x'.repeat(1200)}`;
    renderPrefilled({ name: 'Alert on nginx', filterApps: ['nginx'], referenceMessage: line });
    const ref = screen.getByLabelText('From log') as HTMLTextAreaElement;
    ref.setSelectionRange(0, line.length);
    fireEvent.select(ref);
    expect(screen.getByRole('button', { name: 'Use selection as pattern' })).toBeDisabled();
    expect(screen.getByText(/The selection is too long for a pattern/)).toBeInTheDocument();
  });

  it('treats the prefilled values as the starting point, so closing without edits does not warn', () => {
    renderPrefilled({ name: 'Alert on nginx', filterApps: ['nginx'], referenceMessage: message });
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(screen.queryByText('Unsaved Changes')).toBeNull();
    expect(onClose).toHaveBeenCalled();
  });

  it('ignores the prefill when editing an existing rule', () => {
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={mockRule}
        channels={mockChannels}
        availableApps={['postgres', 'nginx']}
        prefill={{ name: 'Alert on nginx', filterApps: ['nginx'], referenceMessage: message }}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );
    expect(screen.getByPlaceholderText('e.g. Critical Auth Failure Spike')).toHaveValue('Database Timeout Spike');
    expect(screen.queryByTestId('alert-rule-log-reference')).toBeNull();
  });
});

describe('AlertRuleModal host filter', () => {
  const onClose = vi.fn();
  const onSuccess = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  const renderModal = (props: Partial<React.ComponentProps<typeof AlertRuleModal>> = {}) =>
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={['postgres', 'nginx']}
        availableHosts={['nas', 'router', '192.168.1.9']}
        onClose={onClose}
        onSuccess={onSuccess}
        {...props}
      />
    );

  it('saves the hosts picked from the list as one comma-separated filter', async () => {
    const createSpy = vi.spyOn(alertsApi, 'createAlertRule').mockResolvedValue({ ...mockRule, id: 9 });
    renderModal();
    fireEvent.change(screen.getByPlaceholderText('e.g. Critical Auth Failure Spike'), { target: { value: 'NAS' } });
    fireEvent.click(screen.getByRole('button', { name: /All hosts/ }));
    fireEvent.click(screen.getByRole('button', { name: /^nas/, pressed: false }));
    fireEvent.click(screen.getByRole('button', { name: /^router/, pressed: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Create Rule' }));
    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(expect.objectContaining({ name: 'NAS', filter_host: 'nas, router' }))
    );
  });

  it('saves no host filter when none is picked', async () => {
    const createSpy = vi.spyOn(alertsApi, 'createAlertRule').mockResolvedValue({ ...mockRule, id: 9 });
    renderModal();
    fireEvent.change(screen.getByPlaceholderText('e.g. Critical Auth Failure Spike'), { target: { value: 'Any' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Rule' }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(expect.objectContaining({ filter_host: null })));
  });

  it('shows the hosts of a rule it edits, and a change to them counts as unsaved', () => {
    renderModal({ ruleToEdit: { ...mockRule, filter_host: 'nas, 10.0.0.*' } });
    expect(screen.getByTitle('nas, 10.0.0.*')).toHaveTextContent('nas, 10.0.0.*');
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(screen.queryByText('Unsaved Changes')).toBeNull();
  });

  it('warns that a self-reported name can be sent by another device', () => {
    renderModal({
      prefill: { name: 'Printer', filterHosts: ['printer'] },
      availableHosts: ['printer', 'nas'],
      selfReportedHosts: { printer: ['192.168.1.30'] },
    });
    expect(screen.getByTestId('alert-rule-self-reported-host')).toHaveTextContent(
      'printer is a name a device reported for itself, so another device could send it too.'
    );
  });

  it('gives no such warning for an alias', () => {
    renderModal({ prefill: { name: 'NAS', filterHosts: ['nas'] }, selfReportedHosts: { printer: ['192.168.1.30'] } });
    expect(screen.queryByTestId('alert-rule-self-reported-host')).toBeNull();
  });

  it('refuses a host list longer than the server takes', () => {
    renderModal({ prefill: { name: 'Many', filterHosts: Array.from({ length: 30 }, (_, i) => `host-number-${i}`) } });
    expect(screen.getByRole('alert')).toHaveTextContent('The host list is longer than 255 characters.');
    expect(screen.getByRole('button', { name: 'Create Rule' })).toBeDisabled();
  });
});

describe('AlertRuleModal regex or plain text', () => {
  const onClose = vi.fn();
  const onSuccess = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  const renderModal = (props: Partial<React.ComponentProps<typeof AlertRuleModal>> = {}) =>
    render(
      <AlertRuleModal
        isOpen={true}
        ruleToEdit={null}
        channels={mockChannels}
        availableApps={[]}
        onClose={onClose}
        onSuccess={onSuccess}
        {...props}
      />
    );

  const regexBox = () => screen.getByRole('checkbox', { name: 'Match as a regular expression' });

  it('starts a new rule as plain text and saves a Windows path as it is', async () => {
    const createSpy = vi.spyOn(alertsApi, 'createAlertRule').mockResolvedValue({ ...mockRule, id: 9 });
    renderModal();
    expect(regexBox()).not.toBeChecked();
    fireEvent.change(screen.getByPlaceholderText('e.g. Critical Auth Failure Spike'), { target: { value: 'Temp' } });
    fireEvent.change(screen.getByLabelText('Match Pattern'), { target: { value: 'C:\\Temp' } });
    expect(screen.queryByTestId('alert-rule-regex-hint')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Create Rule' }));
    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(expect.objectContaining({ match_pattern: 'C:\\Temp', is_regex: false }))
    );
  });

  it('offers regex matching when the text looks like a regular expression', async () => {
    const createSpy = vi.spyOn(alertsApi, 'createAlertRule').mockResolvedValue({ ...mockRule, id: 9 });
    renderModal();
    fireEvent.change(screen.getByPlaceholderText('e.g. Critical Auth Failure Spike'), { target: { value: 'Auth' } });
    fireEvent.change(screen.getByLabelText('Match Pattern'), { target: { value: 'Failed (password|publickey)' } });
    expect(screen.getByTestId('alert-rule-regex-hint')).toHaveTextContent('This looks like a regular expression.');
    fireEvent.click(screen.getByRole('button', { name: 'Match as a regular expression' }));
    expect(regexBox()).toBeChecked();
    expect(screen.queryByTestId('alert-rule-regex-hint')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Create Rule' }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(expect.objectContaining({ is_regex: true })));
  });

  it('gives the hint for backslash codes but not for plain words', () => {
    renderModal();
    const field = screen.getByLabelText('Match Pattern');
    fireEvent.change(field, { target: { value: 'out of memory' } });
    expect(screen.queryByTestId('alert-rule-regex-hint')).toBeNull();
    fireEvent.change(field, { target: { value: 'error \\d+' } });
    expect(screen.getByTestId('alert-rule-regex-hint')).toBeInTheDocument();
  });

  it('shows the choice of a rule it edits and saves a change to it', async () => {
    const updateSpy = vi.spyOn(alertsApi, 'updateAlertRule').mockResolvedValue({ ...mockRule, is_regex: false });
    renderModal({ ruleToEdit: { ...mockRule, is_regex: true } });
    expect(regexBox()).toBeChecked();
    fireEvent.click(regexBox());
    fireEvent.click(screen.getByRole('button', { name: 'Update Rule' }));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith(42, expect.objectContaining({ is_regex: false })));
  });

  it('counts a changed choice as an unsaved change', () => {
    renderModal({ ruleToEdit: { ...mockRule, is_regex: false } });
    fireEvent.click(regexBox());
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
  });
});

describe('AlertRuleModal AI notices', () => {
  it('writes the AI notices in the normal font, not monospace', () => {
    const { unmount } = render(
      <AlertRuleModal isOpen={true} ruleToEdit={null} channels={mockChannels} availableApps={[]} isAiConfigured={false} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    const notice = screen.getByText(/AI enrichment requires an active AI provider/).parentElement!;
    expect(notice.className).not.toContain('font-mono');
    unmount();
    render(
      <AlertRuleModal isOpen={true} ruleToEdit={mockRule} channels={mockChannels} availableApps={[]} isAiConfigured={false} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    expect(screen.getByText(/AI provider is disabled or not configured/).parentElement!.className).not.toContain('font-mono');
  });
});
