import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { SettingsPanel, pathToSettingsSubTab, settingsSubTabToPath } from '../components/settings/SettingsPanel.tsx';
import * as settingsApi from '../api/settings.ts';
import * as aiApi from '../api/ai.ts';
import * as authApi from '../api/auth.ts';
import * as systemApi from '../api/system.ts';
import * as notifApi from '../api/notifications.ts';
import { DEFAULT_SYSTEM_PROMPT } from '../utils/aiPrompt.ts';

const mockLogout = vi.fn();
vi.mock('../context/AuthContext.tsx', () => ({
  useAuth: () => ({
    logout: mockLogout,
    isAuthenticated: true,
    setupRequired: false,
    isLoading: false,
  }),
}));

describe('SettingsPanel Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.pushState(null, '', '/settings');
    vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
      current_version: '1.1.0-beta.3',
      latest_version: '1.0.0',
      update_available: false,
      checked_at: 1700000000.0,
    });
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    vi.spyOn(aiApi, 'getAiModels').mockResolvedValue({
      provider: 'gemini',
      models: [
        { id: 'gemini-3.7-flash', name: 'Gemini 3.7 Flash', supports_thinking: true },
        { id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', supports_thinking: true },
        { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', supports_thinking: true },
        { id: 'gemini-2.0-flash', name: 'Gemini 2.0 Flash', supports_thinking: false },
        { id: 'gemini-1.5-flash', name: 'Gemini 1.5 Flash', supports_thinking: false },
      ],
      has_api_key: true,
      cached_at: null,
      is_live: true,
    });
    vi.spyOn(notifApi, 'fetchNotificationChannels').mockResolvedValue([]);
  });

  it('renders AI System Instructions card, allows editing and saving ai_system_prompt', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');
    expect((sysTextarea as HTMLTextAreaElement).value).toContain('expert systems engineer');

    // Edit system prompt
    fireEvent.change(sysTextarea, {
      target: { value: 'Custom instructions for homelab root-cause diagnosis.' },
    });

    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          ai_system_prompt: 'Custom instructions for homelab root-cause diagnosis.',
        })
      );
    });
  });

  it('only displays Reset to Default button when AI System Instructions are modified from default', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');

    // On initial load with default prompt, Reset to Default button should NOT be visible
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();

    // Alter system instructions
    fireEvent.change(sysTextarea, {
      target: { value: 'Temporary altered instructions' },
    });
    expect((sysTextarea as HTMLTextAreaElement).value).toBe('Temporary altered instructions');

    // Reset button should now appear
    const resetBtn = screen.getByRole('button', { name: /Reset to Default/i });
    expect(resetBtn).toBeInTheDocument();

    // Clicking Reset button resets instructions and hides the button
    fireEvent.click(resetBtn);
    expect((sysTextarea as HTMLTextAreaElement).value).toContain('expert systems engineer');
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();

    // Modifying again shows button, then typing back default hides it without clicking
    fireEvent.change(sysTextarea, {
      target: { value: 'Another custom instruction' },
    });
    expect(screen.getByRole('button', { name: /Reset to Default/i })).toBeInTheDocument();

    fireEvent.change(sysTextarea, {
      target: { value: DEFAULT_SYSTEM_PROMPT },
    });
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();
  });

  it('displays Reset to Default immediately on load when settings contain a custom system prompt', async () => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      ai_system_prompt: 'Pre-existing custom system prompt from database',
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    // Custom prompt loaded -> Reset to Default button should be visible immediately
    const resetBtn = screen.getByRole('button', { name: /Reset to Default/i });
    expect(resetBtn).toBeInTheDocument();

    // Clicking it resets to DEFAULT_SYSTEM_PROMPT and hides the button
    fireEvent.click(resetBtn);
    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');
    expect((sysTextarea as HTMLTextAreaElement).value).toBe(DEFAULT_SYSTEM_PROMPT);
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();
  });

  it('does not display Reset to Default when text matches default with Windows CRLF line endings', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/AI System Instructions/i)).toBeInTheDocument();
    });

    const sysTextarea = screen.getByPlaceholderText('Enter system instructions...');
    const crlfPrompt = DEFAULT_SYSTEM_PROMPT.replace(/\n/g, '\r\n');

    fireEvent.change(sysTextarea, {
      target: { value: crlfPrompt },
    });

    // Despite CRLF differences, semantic instructions match default so Reset button must NOT appear
    expect(screen.queryByRole('button', { name: /Reset to Default/i })).toBeNull();
  });

  it('keeps Save button hidden on initial load with masked API key, activates on edit, and shows inline feedback on save', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/On-Demand AI Provider Configuration/i)).toBeInTheDocument();
    });

    // Initial state: not dirty, Save Changes button is not rendered
    expect(screen.queryByRole('button', { name: /Save Changes/i })).toBeNull();

    // Edit Default Model Name via dropdown
    const modelSelect = screen.getByDisplayValue(/gemini-3.7-flash/i);
    fireEvent.change(modelSelect, { target: { value: 'gemini-2.5-flash' } });

    // Active state: dirty, button is rendered in the sticky action bar
    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    expect(saveBtn).toBeInTheDocument();
    expect(saveBtn).not.toBeDisabled();

    // Click Save
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          ai_model: 'gemini-2.5-flash',
        })
      );
      // Real-time inline feedback displayed adjacent to button
      expect(screen.getByText('Settings saved successfully!')).toBeInTheDocument();
    });
  });

  it('renders model discovery dropdown and allows selecting discovered models', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/On-Demand AI Provider Configuration/i)).toBeInTheDocument();
      expect(screen.getByDisplayValue(/gemini-3.7-flash/i)).toBeInTheDocument();
    });

    // Gemini provider default model is selected in dropdown
    const geminiSelect = screen.getByDisplayValue(/gemini-3.7-flash/i);
    expect(geminiSelect).toBeInTheDocument();

    // Discovered models include reasoning badges
    expect(screen.getAllByText(/gemini-3.7-flash \[Reasoning\]/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/gemini-2.5-flash \[Reasoning\]/i).length).toBeGreaterThan(0);

    // Switch to OpenAI
    const providerSelect = screen.getByDisplayValue('Google Gemini');
    fireEvent.change(providerSelect, { target: { value: 'openai' } });

    await waitFor(() => {
      expect(aiApi.getAiModels).toHaveBeenCalledWith('openai', false);
    });
  });

  it('renders "Keys encrypted at rest" badge, card title, and explanatory Fernet caption', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('On-Demand AI Provider Configuration')).toBeInTheDocument();
    });

    // Badge
    expect(screen.getByText('Keys encrypted at rest')).toBeInTheDocument();

    // Explanatory caption
    expect(
      screen.getByText(
        'Fernet encryption secures API keys against exposure in database exports, disk clones, and backups.'
      )
    ).toBeInTheDocument();
  });

  it('renders internal log severity dropdown, marks form dirty on change, and saves internal_log_level', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Internal Application Logging')).toBeInTheDocument();
    });

    const select = screen.getByLabelText('Internal Log Severity Threshold') as HTMLSelectElement;
    expect(select.value).toBe('WARNING');

    // Change to ERROR
    fireEvent.change(select, { target: { value: 'ERROR' } });
    expect(select.value).toBe('ERROR');

    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    expect(saveBtn).not.toBeDisabled();

    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          internal_log_level: 'ERROR',
        })
      );
    });
  });

  it('renders and updates fallback models setting', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('Fallback Models (Sequential Order)')).toBeInTheDocument();
    });

    // Select a fallback model from the add dropdown
    const fallbackSelect = screen.getByDisplayValue('-- Add Fallback Model --');
    fireEvent.change(fallbackSelect, { target: { value: 'gemini-2.5-flash' } });

    const addBtn = screen.getByRole('button', { name: /Add/i });
    fireEvent.click(addBtn);

    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
    });

    const saveButton = screen.getByRole('button', { name: /Save Changes/i });
    expect(saveButton).not.toBeDisabled();
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          ai_fallback_models: 'gemini-2.5-flash',
        })
      );
    });
  });

  it('renders configured fallback models with proper ordinal badges (1st, 2nd, 3rd)', async () => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_fallback_models: 'gemini-2.5-flash, gemini-2.5-pro, gemini-1.5-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('2nd Fallback')).toBeInTheDocument();
      expect(screen.getByText('3rd Fallback')).toBeInTheDocument();
    });
  });

  it('filters out fallback models from primary model dropdown and auto-prunes fallback if selected as primary', async () => {
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_fallback_models: 'gemini-2.5-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('1st Fallback')).toBeInTheDocument();
      expect(screen.getByText('gemini-2.5-flash')).toBeInTheDocument();
    });

    // In Primary Model dropdown, gemini-2.5-flash should NOT be available as an option because it is already a fallback
    const primarySelect = screen.getByDisplayValue(/gemini-3.7-flash/);
    const options = Array.from(primarySelect.querySelectorAll('option')).map((o) => o.value);
    expect(options).toContain('gemini-3.7-flash');
    expect(options).not.toContain('gemini-2.5-flash');

    // If user switches to custom mode and enters gemini-2.5-flash as primary
    const useDropdownBtn = screen.queryByRole('button', { name: /Use dropdown instead/i });
    expect(useDropdownBtn).toBeNull();

    // Select custom model option from primary dropdown
    fireEvent.change(primarySelect, { target: { value: '__custom__' } });
    const customInput = screen.getByPlaceholderText(/DEFAULT_AI_MODEL|gemini-3.7-flash/i);
    fireEvent.change(customInput, { target: { value: 'gemini-2.5-flash' } });

    // gemini-2.5-flash should now be pruned from fallback models list
    await waitFor(() => {
      expect(screen.queryByText('1st Fallback')).toBeNull();
      expect(screen.getByText(/No fallback models configured/i)).toBeInTheDocument();
    });
  });

  it('renders sticky floating bar when dirty, and saves settings', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('System Configuration')).toBeInTheDocument();
    });

    // Floating bar should not be present initially
    expect(screen.queryByText('You have unsaved changes')).toBeNull();

    // Modify a field (internal log level)
    const select = screen.getByLabelText('Internal Log Severity Threshold') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'DEBUG' } });

    // Sticky floating bar should appear
    await waitFor(() => {
      expect(screen.getByText('You have unsaved changes')).toBeInTheDocument();
    });

    // Floating bar contains "Save Changes"
    const saveChangesBtn = screen.getByRole('button', { name: /Save Changes/i });
    expect(saveChangesBtn).toBeInTheDocument();

    // Click Save Changes button
    fireEvent.click(saveChangesBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          internal_log_level: 'DEBUG',
        })
      );
    });
  });

  it('resets form values back to baseline and hides dirty bar when Discard is clicked', async () => {
    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByText('System Configuration')).toBeInTheDocument();
    });

    const select = screen.getByLabelText('Internal Log Severity Threshold') as HTMLSelectElement;
    expect(select.value).toBe('WARNING');

    // Change value
    fireEvent.change(select, { target: { value: 'CRITICAL' } });
    expect(select.value).toBe('CRITICAL');

    // Discard button should appear in floating bar
    const discardBtn = screen.getByRole('button', { name: /^Discard$/i });
    expect(discardBtn).toBeInTheDocument();

    // Click Discard
    fireEvent.click(discardBtn);

    // Value should revert to baseline WARNING and floating bar should be removed
    expect(select.value).toBe('WARNING');
    await waitFor(() => {
      expect(screen.queryByText('You have unsaved changes')).toBeNull();
    });
  });

  it('reports dirty state through onDirtyChange callback and triggers beforeunload guard', async () => {
    const onDirtyChange = vi.fn();
    render(<SettingsPanel onDirtyChange={onDirtyChange} />);

    await waitFor(() => {
      expect(screen.getByText('System Configuration')).toBeInTheDocument();
    });

    expect(onDirtyChange).toHaveBeenCalledWith(false);

    // Modify a field
    const select = screen.getByLabelText('Internal Log Severity Threshold');
    fireEvent.change(select, { target: { value: 'INFO' } });

    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenCalledWith(true);
    });

    // Test beforeunload event
    const event = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent;
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);

    // Discard changes
    const discardButton = screen.getByRole('button', { name: /^Discard$/i });
    fireEvent.click(discardButton);

    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
  });

  it('validates password fields and shows error when passwords do not match', async () => {
    render(<SettingsPanel initialSubTab="advanced" />);

    await waitFor(() => {
      expect(screen.getByText('Change Admin Password')).toBeInTheDocument();
    });

    const currentPwdInput = screen.getByLabelText(/Current Password/i);
    const newPwdInput = screen.getByLabelText(/^New Password/i);
    const confirmPwdInput = screen.getByLabelText(/Confirm New Password/i);
    const submitBtn = screen.getByRole('button', { name: /Update Password/i });

    fireEvent.change(currentPwdInput, { target: { value: 'oldpassword123' } });
    fireEvent.change(newPwdInput, { target: { value: 'newpassword123' } });
    fireEvent.change(confirmPwdInput, { target: { value: 'mismatchpassword123' } });

    expect(submitBtn).toBeDisabled();

    const form = currentPwdInput.closest('form')!;
    fireEvent.submit(form);

    await waitFor(() => {
      expect(screen.getByText('New passwords do not match.')).toBeInTheDocument();
    });
  });

  it('handles successful admin password change with login notice and logout redirect', async () => {
    const changePwdSpy = vi.spyOn(authApi, 'changePassword').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel initialSubTab="advanced" />);

    await waitFor(() => {
      expect(screen.getByText('Change Admin Password')).toBeInTheDocument();
    });

    const currentPwdInput = screen.getByLabelText(/Current Password/i);
    const newPwdInput = screen.getByLabelText(/^New Password/i);
    const confirmPwdInput = screen.getByLabelText(/Confirm New Password/i);
    const submitBtn = screen.getByRole('button', { name: /Update Password/i });

    fireEvent.change(currentPwdInput, { target: { value: 'oldpassword123' } });
    fireEvent.change(newPwdInput, { target: { value: 'newpassword123' } });
    fireEvent.change(confirmPwdInput, { target: { value: 'newpassword123' } });

    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(changePwdSpy).toHaveBeenCalledWith('oldpassword123', 'newpassword123');
    });

    expect(sessionStorage.getItem('login_notice')).toBe(
      'Admin password changed successfully. Please sign in with your new password.'
    );
    expect(
      screen.getByText('Admin password changed successfully. Redirecting to sign in...')
    ).toBeInTheDocument();

    await waitFor(
      () => {
        expect(mockLogout).toHaveBeenCalled();
      },
      { timeout: 2000 }
    );
  });

  it('renders About LogShed section on application tab with installed version and up to date status', async () => {
    render(<SettingsPanel initialSubTab="app" />);

    await waitFor(() => {
      expect(screen.getByText('About LogShed')).toBeInTheDocument();
    });

    expect(screen.getByText('v1.1.0-beta.3')).toBeInTheDocument();
    expect(screen.getByText('LogShed is up to date')).toBeInTheDocument();
    expect(screen.getByText(/MIT License - Copyright \(c\) 2026 LogShed Contributors/)).toBeInTheDocument();

    const repoLink = screen.getByRole('link', { name: /GitHub Repository/i });
    expect(repoLink).toHaveAttribute('href', 'https://github.com/logshed/logshed');
    expect(repoLink).toHaveAttribute('target', '_blank');

    const changelogLink = screen.getByRole('link', { name: /Changelog/i });
    expect(changelogLink).toHaveAttribute('href', 'https://github.com/logshed/logshed/blob/main/CHANGELOG.md');
    expect(changelogLink).toHaveAttribute('target', '_blank');
  });

  it('does not render About LogShed section on advanced tab', async () => {
    render(<SettingsPanel initialSubTab="advanced" />);

    await waitFor(() => {
      expect(screen.getByText('Change Admin Password')).toBeInTheDocument();
    });

    expect(screen.queryByText('About LogShed')).not.toBeInTheDocument();
  });

  it('renders App update available notice in About section when newer GHCR version exists', async () => {
    vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
      current_version: '1.1.0-beta.3',
      latest_version: '1.2.0',
      update_available: true,
      check_enabled: true,
      checked_at: 1700000000.0,
    });

    render(<SettingsPanel initialSubTab="app" />);

    await waitFor(() => {
      expect(screen.getByText('About LogShed')).toBeInTheDocument();
    });

    expect(screen.getByText(/App update available:/i)).toBeInTheDocument();
    expect(screen.getByText('v1.2.0')).toBeInTheDocument();

    const releaseNotesLink = screen.getByRole('link', { name: /Release Notes/i });
    expect(releaseNotesLink).toHaveAttribute('href', 'https://github.com/logshed/logshed/releases');
  });

  it('renders Container Repository Moved warning in About section when repo_deprecated is true', async () => {
    vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
      current_version: '1.2.0',
      latest_version: '1.2.0',
      update_available: false,
      check_enabled: true,
      checked_at: 1700000000.0,
      repo_deprecated: true,
    });

    render(<SettingsPanel initialSubTab="app" />);

    await waitFor(() => {
      expect(screen.getByText('Container Repository Moved')).toBeInTheDocument();
    });

    expect(screen.getByText(/ghcr\.io\/benhornertech\/logshed/i)).toBeInTheDocument();
    expect(screen.getByText(/ghcr\.io\/logshed\/logshed/i)).toBeInTheDocument();
  });

  it('renders Update checks are disabled in About section when check_enabled is false', async () => {
    vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
      current_version: '1.1.0-beta.3',
      latest_version: '1.2.0',
      update_available: true,
      check_enabled: false,
      checked_at: 1700000000.0,
    });

    render(<SettingsPanel initialSubTab="app" />);

    await waitFor(() => {
      expect(screen.getByText('About LogShed')).toBeInTheDocument();
    });

    expect(screen.getByText('Update checks are disabled')).toBeInTheDocument();
    expect(screen.queryByText(/App update available:/i)).not.toBeInTheDocument();
  });

  it('allows toggling check_for_updates setting and saving it', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<SettingsPanel />);

    await waitFor(() => {
      expect(screen.getByLabelText('Check for new versions')).toBeInTheDocument();
    });

    const checkbox = screen.getByLabelText('Check for new versions') as HTMLInputElement;
    expect(checkbox.checked).toBe(true);

    // Toggle off
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(false);

    // Save changes
    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          check_for_updates: false,
        })
      );
    });
  });

  describe('Sub-tabs and route helpers', () => {
    it('maps pathname to corresponding SettingsSubTab and vice versa', () => {
      expect(pathToSettingsSubTab('/settings')).toBe('app');
      expect(pathToSettingsSubTab('/settings/')).toBe('app');
      expect(pathToSettingsSubTab('/settings/app')).toBe('app');
      expect(pathToSettingsSubTab('/settings/aliases')).toBe('aliases');
      expect(pathToSettingsSubTab('/aliases')).toBe('aliases');
      expect(pathToSettingsSubTab('/aliases/')).toBe('aliases');
      expect(pathToSettingsSubTab('/settings/advanced')).toBe('advanced');

      expect(settingsSubTabToPath('app')).toBe('/settings/app');
      expect(settingsSubTabToPath('aliases')).toBe('/settings/aliases');
      expect(settingsSubTabToPath('advanced')).toBe('/settings/advanced');
    });

    it('switches between sub-tabs when tab buttons are clicked', async () => {
      render(<SettingsPanel />);

      await waitFor(() => {
        expect(screen.getByText('Internal Application Logging')).toBeInTheDocument();
      });

      // Switch to Host Aliases tab
      const aliasesTab = screen.getByRole('button', { name: /^Host Aliases$/i });
      fireEvent.click(aliasesTab);

      await waitFor(() => {
        expect(screen.getByText('Host Alias Manager')).toBeInTheDocument();
      });
      expect(screen.queryByText('Internal Application Logging')).toBeNull();

      // Switch to Advanced tab
      const advancedTab = screen.getByRole('button', { name: /^Advanced$/i });
      fireEvent.click(advancedTab);

      await waitFor(() => {
        expect(screen.getByText('Change Admin Password')).toBeInTheDocument();
      });
      expect(screen.queryByText('About LogShed')).toBeNull();
      expect(screen.queryByText('Host Alias Manager')).toBeNull();

      // Switch back to Application tab
      const appTab = screen.getByRole('button', { name: /^Application$/i });
      fireEvent.click(appTab);

      await waitFor(() => {
        expect(screen.getByText('Internal Application Logging')).toBeInTheDocument();
        expect(screen.getByText('About LogShed')).toBeInTheDocument();
      });
    });

    it('initializes on given initialSubTab prop', async () => {
      render(<SettingsPanel initialSubTab="aliases" />);

      await waitFor(() => {
        expect(screen.getByText('Host Alias Manager')).toBeInTheDocument();
      });
      expect(screen.queryByText('Internal Application Logging')).toBeNull();
    });

    it('reports dirty state only when App sub-tab is active', async () => {
      const onDirtyChange = vi.fn();
      render(<SettingsPanel onDirtyChange={onDirtyChange} />);

      await waitFor(() => {
        expect(screen.getByText('Internal Application Logging')).toBeInTheDocument();
      });

      // Make change in app subtab
      const select = screen.getByLabelText('Internal Log Severity Threshold');
      fireEvent.change(select, { target: { value: 'ERROR' } });

      await waitFor(() => {
        expect(onDirtyChange).toHaveBeenLastCalledWith(true);
      });

      // Switch to Host Aliases tab
      const aliasesTab = screen.getByRole('button', { name: /^Host Aliases$/i });
      fireEvent.click(aliasesTab);

      await waitFor(() => {
        expect(onDirtyChange).toHaveBeenLastCalledWith(false);
      });

      // Switch back to Application tab
      const appTab = screen.getByRole('button', { name: /^Application$/i });
      fireEvent.click(appTab);

      await waitFor(() => {
        expect(onDirtyChange).toHaveBeenLastCalledWith(true);
      });
    });
  });

  describe('AI Enablement Toggle and Section Visibility', () => {
    it('hides AI configuration fields and displays advisory notice when ai_enabled is false', async () => {
      vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
        ai_enabled: false,
        ai_provider: 'gemini',
        ai_model: 'gemini-3.7-flash',
        ai_api_key: '',
        ai_base_url: null,
        retention_days: 14,
        max_retention_days: 30,
        has_ai_api_key: false,
      });

      render(<SettingsPanel />);

      await waitFor(() => {
        expect(screen.getByText(/Enable AI Features/i)).toBeInTheDocument();
      });

      const aiCheckbox = screen.getByRole('checkbox', { name: /Enable AI Features/i });
      expect(aiCheckbox).not.toBeChecked();

      // Advisory notice is rendered
      expect(screen.getByText(/AI Features Disabled/i)).toBeInTheDocument();
      expect(
        screen.getByText(/Existing alert rules configured with AI enrichment will continue to trigger/i)
      ).toBeInTheDocument();

      // AI configuration fields should NOT be rendered
      expect(screen.queryByText(/^AI Provider$/)).toBeNull();
      expect(screen.queryByPlaceholderText('Enter system instructions...')).toBeNull();
    });

    it('toggles AI enablement, reveals fields when checked, and saves ai_enabled setting', async () => {
      vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
        ai_enabled: false,
        ai_provider: 'gemini',
        ai_model: 'gemini-3.7-flash',
        ai_api_key: '',
        ai_base_url: null,
        retention_days: 14,
        max_retention_days: 30,
        has_ai_api_key: false,
      });
      const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

      render(<SettingsPanel />);

      await waitFor(() => {
        expect(screen.getByRole('checkbox', { name: /Enable AI Features/i })).toBeInTheDocument();
      });

      const aiCheckbox = screen.getByRole('checkbox', { name: /Enable AI Features/i });
      expect(aiCheckbox).not.toBeChecked();

      // Check the box
      fireEvent.click(aiCheckbox);
      expect(aiCheckbox).toBeChecked();

      // AI configuration fields should now appear
      await waitFor(() => {
        expect(screen.getByText('AI Provider')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('Enter system instructions...')).toBeInTheDocument();
      });

      // Save changes
      const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
      fireEvent.click(saveBtn);

      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(
          expect.objectContaining({
            ai_enabled: true,
          })
        );
      });
    });

    it('allows editing daily digest settings and saving via the sticky Save Changes button', async () => {
      vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
        ai_enabled: true,
        ai_provider: 'gemini',
        ai_model: 'gemini-3.7-flash',
        ai_api_key: '********',
        ai_base_url: null,
        retention_days: 14,
        max_retention_days: 30,
        has_ai_api_key: true,
        daily_digest_enabled: false,
        daily_digest_channel_id: null,
        daily_digest_schedule_time: '09:00',
      });
      vi.spyOn(notifApi, 'fetchNotificationChannels').mockResolvedValue([
        {
          id: 1,
          name: 'Homelab Discord',
          url: 'discord://1...9/a...f',
          is_enabled: true,
          created_at: '2026-09-18T12:00:00Z',
          updated_at: '2026-09-18T12:00:00Z',
        },
      ]);
      const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

      render(<SettingsPanel />);

      await waitFor(() => {
        expect(screen.getByText('Daily Digest Rollup')).toBeInTheDocument();
      });

      // No unsaved changes initially
      expect(screen.queryByRole('button', { name: /Save Changes/i })).toBeNull();

      // Check daily digest checkbox
      const digestCheckbox = screen.getByLabelText(/Enable 24-hour daily digest rollup/i);
      fireEvent.click(digestCheckbox);
      expect(digestCheckbox).toBeChecked();

      // Save button should now be visible in sticky action bar
      const saveBtn = await screen.findByRole('button', { name: /Save Changes/i });
      expect(saveBtn).toBeInTheDocument();

      // Change schedule time
      const timeInput = screen.getByLabelText(/Schedule Time/i);
      fireEvent.change(timeInput, { target: { value: '18:30' } });

      // Click Save Changes
      fireEvent.click(saveBtn);

      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(
          expect.objectContaining({
            daily_digest_enabled: true,
            daily_digest_schedule_time: '18:30',
          })
        );
      });
    });
  });
});


