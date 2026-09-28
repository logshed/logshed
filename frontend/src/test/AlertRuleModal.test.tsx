import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AlertRuleModal } from '../components/alerts/AlertRuleModal.tsx';
import * as alertsApi from '../api/alerts.ts';
import { AlertRule, NotificationChannel } from '../types.ts';

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
});
