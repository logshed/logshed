import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AdvancedSettingsCard } from '../components/settings/AdvancedSettingsCard.tsx';
import * as settingsApi from '../api/settings.ts';
import { SystemSettings } from '../types.ts';

vi.mock('../api/settings.ts', () => ({
  updateSettings: vi.fn(),
  fetchSettings: vi.fn(),
}));

const mockBaseSettings: SystemSettings = {
  ai_provider: 'gemini',
  ai_model: 'gemini-3.7-flash',
  ai_fallback_models: '',
  ai_base_url: null,
  ai_system_prompt: 'System prompt',
  retention_days: 14,
  max_retention_days: 30,
  retention_overridden: false,
  internal_log_level: 'WARNING',
  check_for_updates: true,
  maintenance_until: null,
  ai_timeout: 45.0,
  ai_thinking_budget: 1024,
  app_url: 'https://logshed.lan',
  allow_private_notification_targets: true,
  enable_docker: true,
  docker_exclude_containers: 'cadvisor',
  docker_source_alias: 'docker',
  trusted_proxies: '172.16.0.0/12',
  trust_docker_proxies: false,
  cookie_secure: false,
  syslog_max_tcp_connections: 250,
  syslog_tcp_inactivity_timeout: 0.0,
};

describe('AdvancedSettingsCard Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders with populated setting values', () => {
    render(
      <AdvancedSettingsCard
        settings={mockBaseSettings}
        onSettingsSaved={vi.fn()}
      />
    );

    expect(screen.getByDisplayValue('45')).toBeInTheDocument();
    expect(screen.getByDisplayValue('1024')).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://logshed.lan')).toBeInTheDocument();
    expect(screen.getByDisplayValue('cadvisor')).toBeInTheDocument();
    expect(screen.getByDisplayValue('docker')).toBeInTheDocument();
    expect(screen.getByDisplayValue('172.16.0.0/12')).toBeInTheDocument();
    expect(screen.getByDisplayValue('250')).toBeInTheDocument();
  });

  it('detects dirty state when an input is edited, displaying floating save bar', async () => {
    const onSaved = vi.fn();
    const onDirty = vi.fn();
    vi.mocked(settingsApi.updateSettings).mockResolvedValueOnce({
      status: 'ok',
    });

    render(
      <AdvancedSettingsCard
        settings={mockBaseSettings}
        onSettingsSaved={onSaved}
        onDirtyChange={onDirty}
      />
    );

    // Initial state: not dirty, floating bar is not visible
    expect(screen.queryByLabelText(/Unsaved changes bar/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Save Changes/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Discard/i })).not.toBeInTheDocument();

    // Edit AI timeout
    const timeoutInput = screen.getByDisplayValue('45');
    fireEvent.change(timeoutInput, { target: { value: '60' } });

    // Now dirty: floating bar appears with Discard and Save Changes
    expect(screen.getByLabelText(/Unsaved changes bar/i)).toBeInTheDocument();
    expect(screen.getByText(/You have unsaved changes/i)).toBeInTheDocument();
    const saveButton = screen.getByRole('button', { name: /Save Changes/i });
    const discardButton = screen.getByRole('button', { name: /Discard/i });
    expect(saveButton).toBeInTheDocument();
    expect(discardButton).toBeInTheDocument();

    // Click Save Changes
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(settingsApi.updateSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          ai_timeout: 60,
        })
      );
      expect(onSaved).toHaveBeenCalled();
      expect(screen.getByText(/Settings saved successfully!/i)).toBeInTheDocument();
    });
  });

  it('discards changes when Discard button is clicked', () => {
    render(
      <AdvancedSettingsCard
        settings={mockBaseSettings}
        onSettingsSaved={vi.fn()}
      />
    );

    // Initial state: no floating bar
    expect(screen.queryByLabelText(/Unsaved changes bar/i)).not.toBeInTheDocument();

    const timeoutInput = screen.getByDisplayValue('45');
    fireEvent.change(timeoutInput, { target: { value: '99' } });
    expect(screen.getByDisplayValue('99')).toBeInTheDocument();

    const discardButton = screen.getByRole('button', { name: /Discard/i });
    expect(discardButton).toBeInTheDocument();
    fireEvent.click(discardButton);

    // Reverts back to initial 45 and floating bar is dismissed
    expect(screen.getByDisplayValue('45')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Unsaved changes bar/i)).not.toBeInTheDocument();
  });

  it('only sends modified fields when toggling container tailing', async () => {
    const onSaved = vi.fn();
    vi.mocked(settingsApi.updateSettings).mockResolvedValueOnce({
      status: 'ok',
    });

    render(
      <AdvancedSettingsCard
        settings={mockBaseSettings}
        onSettingsSaved={onSaved}
      />
    );

    const dockerCheckbox = screen.getByLabelText(/Enable Container Tailing/i);
    fireEvent.click(dockerCheckbox);

    const saveButton = screen.getByRole('button', { name: /Save Changes/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(settingsApi.updateSettings).toHaveBeenCalledWith({
        enable_docker: false,
      });
      expect(onSaved).toHaveBeenCalled();
    });
  });
});
