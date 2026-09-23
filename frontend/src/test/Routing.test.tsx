import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { App, pathToTab, tabToPath } from '../App.tsx';
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

vi.mock('../components/storage/StoragePanel.tsx', () => ({
  StoragePanel: () => <div data-testid="storage-panel">Storage Content</div>,
}));

vi.mock('../components/alerts/AlertsPanel.tsx', () => ({
  AlertsPanel: () => <div data-testid="alerts-panel">Alerts Content</div>,
}));

describe('URL Routing and History API Synchronization', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      database: 'ok',
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
      retention_overridden: false,
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

  afterEach(() => {
    window.history.pushState(null, '', '/');
  });

  describe('Route helper mapping', () => {
    it('maps pathname to corresponding AppTab', () => {
      expect(pathToTab('/')).toBe('stream');
      expect(pathToTab('/console')).toBe('stream');
      expect(pathToTab('/aliases')).toBe('settings');
      expect(pathToTab('/aliases/')).toBe('settings');
      expect(pathToTab('/storage')).toBe('storage');
      expect(pathToTab('/alerts')).toBe('alerts');
      expect(pathToTab('/alerts/rules')).toBe('alerts');
      expect(pathToTab('/alerts/presets')).toBe('alerts');
      expect(pathToTab('/alerts/quick-rules')).toBe('alerts');
      expect(pathToTab('/alerts/history')).toBe('alerts');
      expect(pathToTab('/settings')).toBe('settings');
      expect(pathToTab('/settings/app')).toBe('settings');
      expect(pathToTab('/settings/aliases')).toBe('settings');
      expect(pathToTab('/settings/advanced')).toBe('settings');
      expect(pathToTab('/unknown-path')).toBe('stream');
    });

    it('maps AppTab to canonical URL path', () => {
      expect(tabToPath('stream')).toBe('/');
      expect(tabToPath('storage')).toBe('/storage');
      expect(tabToPath('alerts')).toBe('/alerts');
      expect(tabToPath('settings')).toBe('/settings');
    });
  });

  describe('App history synchronization', () => {
    it('initializes on settings tab when URL pathname is /settings', async () => {
      window.history.pushState(null, '', '/settings');
      render(<App />);

      await waitFor(() => {
        expect(screen.getByText('System Configuration')).toBeInTheDocument();
      });
      expect(screen.queryByTestId('live-log-stream')).toBeNull();
    });

    it('initializes on storage tab when URL pathname is /storage', async () => {
      window.history.pushState(null, '', '/storage');
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId('storage-panel')).toBeInTheDocument();
      });
    });

    it('initializes on settings aliases sub-tab when URL pathname is /aliases (backward compatibility)', async () => {
      window.history.pushState(null, '', '/aliases');
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId('host-alias-manager')).toBeInTheDocument();
      });
    });

    it('initializes on alerts tab when URL pathname is /alerts or /alerts/history', async () => {
      window.history.pushState(null, '', '/alerts/history');
      render(<App />);

      await waitFor(() => {
        expect(screen.getByTestId('alerts-panel')).toBeInTheDocument();
      });
    });

    it('updates URL pathname when user clicks navigation tabs', async () => {
      window.history.pushState(null, '', '/');
      const pushStateSpy = vi.spyOn(window.history, 'pushState');

      render(<App />);
      expect(screen.getByTestId('live-log-stream')).toBeInTheDocument();

      // Click Alerts & Rules tab
      const alertsBtn = screen.getByRole('button', { name: /^Alerts & Rules$/i });
      await act(async () => {
        fireEvent.click(alertsBtn);
      });

      expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/alerts');
      expect(screen.getByTestId('alerts-panel')).toBeInTheDocument();

      // Click Storage tab
      const storageBtn = screen.getByRole('button', { name: /^Storage$/i });
      await act(async () => {
        fireEvent.click(storageBtn);
      });

      expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/storage');
      expect(screen.getByTestId('storage-panel')).toBeInTheDocument();

      // Click Settings tab
      const settingsBtn = screen.getByRole('button', { name: /^Settings$/i });
      await act(async () => {
        fireEvent.click(settingsBtn);
      });

      expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/settings');
      expect(screen.getByText('System Configuration')).toBeInTheDocument();

      // Click Brand logo to return to console stream
      const brandLogo = screen.getByTitle('Go to Console View');
      await act(async () => {
        fireEvent.click(brandLogo);
      });

      expect(pushStateSpy).toHaveBeenCalledWith(null, '', '/');
      expect(screen.getByTestId('live-log-stream')).toBeInTheDocument();
    });

    it('switches tabs on popstate event (browser back and forward navigation)', async () => {
      window.history.pushState(null, '', '/');
      render(<App />);
      expect(screen.getByTestId('live-log-stream')).toBeInTheDocument();

      // Simulate user navigating to /storage via browser back/forward
      act(() => {
        window.history.pushState(null, '', '/storage');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      await waitFor(() => {
        expect(screen.getByTestId('storage-panel')).toBeInTheDocument();
      });

      // Simulate user navigating to /aliases (backward compatibility redirects to settings aliases)
      act(() => {
        window.history.pushState(null, '', '/aliases');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      await waitFor(() => {
        expect(screen.getByTestId('host-alias-manager')).toBeInTheDocument();
      });
    });
  });

  describe('Dynamic browser title for beta / prerelease builds', () => {
    it('sets document.title with version bracketed for prerelease versions', async () => {
      vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
        current_version: '1.2.0-beta.1',
        latest_version: '1.2.0-beta.1',
        update_available: false,
      });

      render(<App />);

      await waitFor(() => {
        expect(document.title).toBe('LogShed [1.2.0-beta.1]');
      });
    });

    it('retains clean LogShed title for stable versions without hyphen', async () => {
      vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
        current_version: '1.2.0',
        latest_version: '1.2.0',
        update_available: false,
      });

      render(<App />);

      await waitFor(() => {
        expect(document.title).toBe('LogShed');
      });
    });
  });
});
