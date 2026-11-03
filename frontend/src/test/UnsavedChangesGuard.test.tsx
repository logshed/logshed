import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { App } from '../App.tsx';
import * as settingsApi from '../api/settings.ts';
import * as aiApi from '../api/ai.ts';
import * as systemApi from '../api/system.ts';

vi.mock('../context/AuthContext.tsx', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    setupRequired: false,
    isLoading: false,
    logout: vi.fn(),
  }),
}));

vi.mock('../components/logs/LiveLogStream.tsx', () => ({
  LiveLogStream: () => <div data-testid="live-log-stream">Console View Content</div>,
}));

vi.mock('../components/aliases/HostAliasManager.tsx', () => ({
  HostAliasManager: () => <div data-testid="host-alias-manager">Host Alias Content</div>,
}));

vi.mock('../components/storage/StoragePanel.tsx', () => {
  const StoragePanel = () => <div data-testid="storage-panel">Storage Content</div>;
  return { StoragePanel, default: StoragePanel };
});

describe('UnsavedChangesGuard Integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });

    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      internal_log_level: 'WARNING',
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    vi.spyOn(aiApi, 'getAiModels').mockResolvedValue({
      provider: 'gemini',
      models: [
        { id: 'gemini-3.7-flash', name: 'Gemini 3.7 Flash', supports_thinking: true },
      ],
      has_api_key: true,
      cached_at: null,
      is_live: true,
    });
  });

  it('triggers confirmation modal when navigating away with unsaved settings', async () => {
    render(<App />);

    // Start on stream/console view
    expect(screen.getByTestId('live-log-stream')).toBeInTheDocument();

    // Navigate to Settings
    const settingsTabBtn = screen.getByRole('button', { name: /^Settings$/i });
    fireEvent.click(settingsTabBtn);

    await waitFor(() => {
      expect(screen.getByText('System Configuration')).toBeInTheDocument();
    });

    // Make an edit
    const select = screen.getByLabelText('Internal Log Severity Threshold') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'ERROR' } });

    // Attempt to navigate to Storage tab
    const storageTabBtn = screen.getByRole('button', { name: /Storage/i });
    fireEvent.click(storageTabBtn);

    // Confirmation modal should appear and navigation should be blocked
    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
      expect(
        screen.getByText(/You have unsaved changes in System Configuration. Leaving now will discard those edits./i)
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId('storage-panel')).toBeNull();
  });

  it('stays on settings tab when "Keep Editing" is clicked', async () => {
    render(<App />);

    const settingsTabBtn = screen.getByRole('button', { name: /^Settings$/i });
    fireEvent.click(settingsTabBtn);

    await waitFor(() => {
      expect(screen.getByText('System Configuration')).toBeInTheDocument();
    });

    // Make an edit
    const select = screen.getByLabelText('Internal Log Severity Threshold');
    fireEvent.change(select, { target: { value: 'ERROR' } });

    // Attempt to navigate to Console View
    const consoleTabBtn = screen.getByRole('button', { name: /Console View/i });
    fireEvent.click(consoleTabBtn);

    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    });

    // Click "Keep Editing"
    const keepEditingBtn = screen.getByRole('button', { name: /Keep Editing/i });
    fireEvent.click(keepEditingBtn);

    // Modal closes and user remains on Settings
    await waitFor(() => {
      expect(screen.queryByText('Unsaved Changes')).toBeNull();
    });
    expect(screen.getByText('System Configuration')).toBeInTheDocument();
    expect(screen.queryByTestId('live-log-stream')).toBeNull();
  });

  it('discards edits and navigates to target tab when "Discard & Leave" is clicked', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<App />);

    const settingsTabBtn = screen.getByRole('button', { name: /^Settings$/i });
    fireEvent.click(settingsTabBtn);

    await waitFor(() => {
      expect(screen.getByText('System Configuration')).toBeInTheDocument();
    });

    // Make an edit
    const select = screen.getByLabelText('Internal Log Severity Threshold');
    fireEvent.change(select, { target: { value: 'ERROR' } });

    // Attempt to navigate to Storage
    const storageTabBtn = screen.getByRole('button', { name: /Storage/i });
    fireEvent.click(storageTabBtn);

    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    });

    // Click "Discard & Leave"
    const discardLeaveBtn = screen.getByRole('button', { name: /Discard & Leave/i });
    fireEvent.click(discardLeaveBtn);

    // Navigates to Storage and does not call updateSettings
    await waitFor(() => {
      expect(screen.queryByText('Unsaved Changes')).toBeNull();
      expect(screen.getByTestId('storage-panel')).toBeInTheDocument();
    });
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it('saves changes and navigates to target tab when "Save & Continue" is clicked', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<App />);

    const settingsTabBtn = screen.getByRole('button', { name: /^Settings$/i });
    fireEvent.click(settingsTabBtn);

    await waitFor(() => {
      expect(screen.getByText('System Configuration')).toBeInTheDocument();
    });

    // Make an edit
    const select = screen.getByLabelText('Internal Log Severity Threshold');
    fireEvent.change(select, { target: { value: 'DEBUG' } });

    // Attempt to navigate to Console View
    const consoleTabBtn = screen.getByRole('button', { name: /Console View/i });
    fireEvent.click(consoleTabBtn);

    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    });

    // Click "Save & Continue"
    const saveContinueBtn = screen.getByRole('button', { name: /Save & Continue/i });
    fireEvent.click(saveContinueBtn);

    // Settings are saved and navigation completes
    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          internal_log_level: 'DEBUG',
        })
      );
      expect(screen.queryByText('Unsaved Changes')).toBeNull();
      expect(screen.getByTestId('live-log-stream')).toBeInTheDocument();
    });
  });

  it('intercepts brand logo click when unsaved changes exist', async () => {
    render(<App />);

    const settingsTabBtn = screen.getByRole('button', { name: /^Settings$/i });
    fireEvent.click(settingsTabBtn);

    await waitFor(() => {
      expect(screen.getByText('System Configuration')).toBeInTheDocument();
    });

    // Make an edit
    const select = screen.getByLabelText('Internal Log Severity Threshold');
    fireEvent.change(select, { target: { value: 'ERROR' } });

    // Click brand logo (title="Go to Console View")
    const brandLogo = screen.getByTitle('Go to Console View');
    fireEvent.click(brandLogo);

    await waitFor(() => {
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('live-log-stream')).toBeNull();
  });

  describe('other sub-tabs and the Advanced tab', () => {
    beforeEach(() => {
      vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
        ai_provider: 'gemini',
        ai_model: 'gemini-3.7-flash',
        ai_api_key: '********',
        ai_base_url: null,
        internal_log_level: 'WARNING',
        retention_days: 14,
        max_retention_days: 30,
        has_ai_api_key: true,
        ai_timeout: 45,
        ai_thinking_level: 'low',
        app_url: '',
        trusted_proxies: '',
        syslog_max_tcp_connections: 250,
        syslog_tcp_inactivity_timeout: 0,
      });
    });

    const openSettings = async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Settings$/i }));
      await waitFor(() => {
        expect(screen.getByText('System Configuration')).toBeInTheDocument();
      });
    };

    it('still asks before leaving Settings when an Application tab edit is open on another sub-tab', async () => {
      render(<App />);
      await openSettings();

      fireEvent.change(screen.getByLabelText('Internal Log Severity Threshold'), { target: { value: 'ERROR' } });
      fireEvent.click(screen.getByRole('button', { name: /^Advanced$/i }));
      await waitFor(() => {
        expect(screen.getByText('Advanced System Settings')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Storage/i }));
      await waitFor(() => {
        expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
      });
      expect(screen.queryByTestId('storage-panel')).toBeNull();
    });

    it('asks before leaving Settings with unsaved Advanced edits, and Save & Continue saves them', async () => {
      const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });
      render(<App />);
      await openSettings();

      fireEvent.click(screen.getByRole('button', { name: /^Advanced$/i }));
      await waitFor(() => {
        expect(screen.getByDisplayValue('45')).toBeInTheDocument();
      });
      fireEvent.change(screen.getByDisplayValue('45'), { target: { value: '90' } });

      fireEvent.click(screen.getByRole('button', { name: /Storage/i }));
      await waitFor(() => {
        expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
      });
      expect(screen.queryByTestId('storage-panel')).toBeNull();

      fireEvent.click(screen.getByRole('button', { name: /Save & Continue/i }));
      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(expect.objectContaining({ ai_timeout: 90 }));
        expect(screen.getByTestId('storage-panel')).toBeInTheDocument();
      });
    });

    it('switching away from an Advanced tab with unsaved edits asks first, and Discard & Leave switches', async () => {
      render(<App />);
      await openSettings();

      fireEvent.click(screen.getByRole('button', { name: /^Advanced$/i }));
      await waitFor(() => {
        expect(screen.getByDisplayValue('45')).toBeInTheDocument();
      });
      fireEvent.change(screen.getByDisplayValue('45'), { target: { value: '90' } });

      fireEvent.click(screen.getByRole('button', { name: /^Application$/i }));
      await waitFor(() => {
        expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
      });
      expect(screen.getByText('Advanced System Settings')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /Keep Editing/i }));
      await waitFor(() => {
        expect(screen.queryByText('Unsaved Changes')).toBeNull();
      });
      expect(screen.getByDisplayValue('90')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /^Application$/i }));
      await waitFor(() => {
        expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByRole('button', { name: /Discard & Leave/i }));
      await waitFor(() => {
        expect(screen.queryByText('Advanced System Settings')).toBeNull();
        expect(screen.getByLabelText('Internal Log Severity Threshold')).toBeInTheDocument();
      });
    });
  });
});

describe('Unsaved changes guard keeps the URL of a link it held back', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, '', '/');
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      internal_log_level: 'WARNING',
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });
    vi.spyOn(aiApi, 'getAiModels').mockResolvedValue({
      provider: 'gemini',
      models: [{ id: 'gemini-3.7-flash', name: 'Gemini 3.7 Flash', supports_thinking: true }],
      has_api_key: true,
      cached_at: null,
      is_live: true,
    });
  });

  /** Opens Settings, makes an unsaved edit, then follows a link such as "View logs" on a host alias. */
  const followLinkWithUnsavedEdits = async (url: string) => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /^Settings$/i }));
    await waitFor(() => expect(screen.getByText('System Configuration')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Internal Log Severity Threshold'), { target: { value: 'ERROR' } });
    act(() => {
      window.history.pushState(null, '', url);
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await waitFor(() => expect(screen.getByText('Unsaved Changes')).toBeInTheDocument());
    // While the prompt is open the address stays on Settings
    expect(window.location.pathname.startsWith('/settings')).toBe(true);
  };

  it('Discard & Leave opens the link with its filters', async () => {
    await followLinkWithUnsavedEdits('/?source=nas');
    fireEvent.click(screen.getByRole('button', { name: /Discard & Leave/i }));
    await waitFor(() => expect(screen.getByTestId('live-log-stream')).toBeInTheDocument());
    expect(window.location.pathname + window.location.search).toBe('/?source=nas');
  });

  it('Save & Continue opens the link with its filters', async () => {
    vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });
    await followLinkWithUnsavedEdits('/?source=nas&app=nginx');
    fireEvent.click(screen.getByRole('button', { name: /Save & Continue/i }));
    await waitFor(() => expect(screen.getByTestId('live-log-stream')).toBeInTheDocument());
    expect(window.location.pathname + window.location.search).toBe('/?source=nas&app=nginx');
  });

  it('Keep Editing stays on Settings, and a later tab click goes to the plain tab path', async () => {
    await followLinkWithUnsavedEdits('/?source=nas');
    fireEvent.click(screen.getByRole('button', { name: /Keep Editing/i }));
    await waitFor(() => expect(screen.queryByText('Unsaved Changes')).toBeNull());
    expect(screen.getByText('System Configuration')).toBeInTheDocument();
    expect(window.location.search).toBe('');

    // The held-back link is forgotten: a Navbar click afterwards does not reuse it
    fireEvent.click(screen.getByRole('button', { name: /Console View/i }));
    await waitFor(() => expect(screen.getByText('Unsaved Changes')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Discard & Leave/i }));
    await waitFor(() => expect(screen.getByTestId('live-log-stream')).toBeInTheDocument());
    expect(window.location.pathname + window.location.search).toBe('/');
  });
});
