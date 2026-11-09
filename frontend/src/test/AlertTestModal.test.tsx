import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AlertTestModal } from '../components/alerts/AlertTestModal.tsx';
import * as alertsApi from '../api/alerts.ts';
import { AlertRule } from '../types.ts';
import { chooseOption, optionLabels } from './selectDropdownHelpers.ts';

const mockRule: AlertRule = {
  id: 1,
  name: 'SSH Brute-Force Detection',
  rule_type: 'threshold',
  channel_id: 1,
  filter_app: 'sshd',
  filter_severity: 6,
  match_pattern: 'Failed password',
  threshold_count: 5,
  window_seconds: 60,
  cooldown_seconds: 300,
  ai_enrichment: false,
  is_enabled: true,
  trigger_count: 0,
  last_triggered_at: null,
  suppress_until: null,
  created_at: '2026-09-18T09:00:00Z',
};

describe('AlertTestModal Component', () => {
  const onClose = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('does not render when isOpen is false', () => {
    render(
      <AlertTestModal
        isOpen={false}
        rule={mockRule}
        availableApps={['sshd']}
        onClose={onClose}
      />
    );

    expect(screen.queryByRole('heading', { name: /Dry-Run Test:/i })).toBeNull();
  });

  it('renders test modal and runs pattern test successfully with extracted IP', async () => {
    const testSpy = vi.spyOn(alertsApi, 'testAlertRule').mockResolvedValue({
      matched: true,
      extracted_ip: '10.0.0.15',
    });

    render(
      <AlertTestModal
        isOpen={true}
        rule={mockRule}
        availableApps={['sshd']}
        onClose={onClose}
      />
    );

    expect(screen.getByRole('heading', { name: 'Dry-Run Test: SSH Brute-Force Detection' })).toBeInTheDocument();
    expect(screen.getByText('Failed password')).toBeInTheDocument();

    const runBtn = screen.getByRole('button', { name: 'Run Test' });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          rule_type: 'threshold',
          match_pattern: 'Failed password',
          sample_app: 'sshd',
        })
      );
      expect(screen.getByText('Pattern Matched Successfully')).toBeInTheDocument();
      expect(screen.getByText('10.0.0.15')).toBeInTheDocument();
    });
  });

  it('handles negative pattern match gracefully', async () => {
    vi.spyOn(alertsApi, 'testAlertRule').mockResolvedValue({
      matched: false,
    });

    render(
      <AlertTestModal
        isOpen={true}
        rule={mockRule}
        availableApps={['sshd']}
        onClose={onClose}
      />
    );

    const runBtn = screen.getByRole('button', { name: 'Run Test' });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(screen.getByText('No Match Found')).toBeInTheDocument();
    });
  });

  it('calls onClose when clicking close button', () => {
    render(
      <AlertTestModal
        isOpen={true}
        rule={mockRule}
        availableApps={['sshd']}
        onClose={onClose}
      />
    );

    const closeBtn = screen.getByRole('button', { name: 'Close' });
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalled();
  });

  it('sends the rule filters, not the sample values, and shows not matched', async () => {
    const testSpy = vi.spyOn(alertsApi, 'testAlertRule').mockImplementation(async (req) => ({
      matched: req.filter_app === req.sample_app,
    }));
    const rule: AlertRule = { ...mockRule, filter_app: 'nginx', filter_severity: 3 };

    render(<AlertTestModal isOpen={true} rule={rule} availableApps={['nginx', 'sshd']} onClose={onClose} />);

    const appInput = screen.getByLabelText('Sample App');
    fireEvent.change(appInput, { target: { value: 'sshd' } });
    chooseOption(screen.getByLabelText('Sample Severity'), '6 - Info');
    fireEvent.click(screen.getByRole('button', { name: 'Run Test' }));

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          filter_app: 'nginx',
          filter_severity: 3,
          sample_app: 'sshd',
          sample_severity: 6,
        })
      );
      expect(screen.getByText('No Match Found')).toBeInTheDocument();
    });
  });

  it('sends null filters when the rule has none', async () => {
    const testSpy = vi.spyOn(alertsApi, 'testAlertRule').mockResolvedValue({ matched: true });
    const rule: AlertRule = { ...mockRule, filter_app: null, filter_severity: null };

    render(<AlertTestModal isOpen={true} rule={rule} availableApps={[]} onClose={onClose} />);
    fireEvent.click(screen.getByRole('button', { name: 'Run Test' }));

    await waitFor(() => {
      expect(testSpy).toHaveBeenCalledWith(
        expect.objectContaining({ filter_app: null, filter_severity: null })
      );
    });
  });

  it('offers all 8 severities for the sample', () => {
    render(<AlertTestModal isOpen={true} rule={mockRule} availableApps={[]} onClose={onClose} />);
    expect(optionLabels(screen.getByLabelText('Sample Severity'))).toEqual([
      '0 - Emergency',
      '1 - Alert',
      '2 - Critical',
      '3 - Error',
      '4 - Warning',
      '5 - Notice',
      '6 - Info',
      '7 - Debug',
    ]);
  });
});
