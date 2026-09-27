import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { Navbar } from '../components/common/Navbar.tsx';
import * as systemApi from '../api/system.ts';
import * as alertsApi from '../api/alerts.ts';

vi.mock('../context/AuthContext.tsx', () => ({
  useAuth: () => ({
    logout: vi.fn(),
    isAuthenticated: true,
  }),
}));

describe('Navbar Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('navigates to console view when brand logo is clicked', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      database: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });

    const onTabChange = vi.fn();
    const onSelectTab = vi.fn();

    render(
      <Navbar
        activeTab="settings"
        onTabChange={onTabChange}
        onSelectTab={onSelectTab}
      />
    );

    await waitFor(() => {
      expect(systemApi.fetchHealth).toHaveBeenCalled();
    });

    const brandLogo = screen.getByTitle('Go to Console View');
    fireEvent.click(brandLogo);

    expect(onTabChange).toHaveBeenCalledWith('stream');
    expect(onSelectTab).toHaveBeenCalledWith('console');
  });

  it('does not render LIVE / IDLE indicator and displays live rate when health is fetched', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      database: 'ok',
      queue_depth: 42,
      dropped_logs: 0,
      ingest_rate: 18.5,
    });

    const onTabChange = vi.fn();

    render(
      <Navbar
        activeTab="stream"
        onTabChange={onTabChange}
      />
    );

    // Ensure LIVE / IDLE indicator is absent
    expect(screen.queryByText('LIVE')).toBeNull();
    expect(screen.queryByText('IDLE')).toBeNull();

    // Verify rate, queue, and dropped displays
    await waitFor(() => {
      expect(screen.getByText('18.5 logs/s')).toBeInTheDocument();
      expect(screen.getByText('42')).toBeInTheDocument();
    });
  });

  it('supports Enter and Space keyboard activation on brand logo', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });

    const onTabChange = vi.fn();
    const onSelectTab = vi.fn();

    render(
      <Navbar
        activeTab="settings"
        onTabChange={onTabChange}
        onSelectTab={onSelectTab}
      />
    );

    await waitFor(() => {
      expect(systemApi.fetchHealth).toHaveBeenCalled();
    });

    const brandLogo = screen.getByTitle('Go to Console View');
    expect(brandLogo).toHaveAttribute('role', 'button');
    expect(brandLogo).toHaveAttribute('tabIndex', '0');

    // Test Enter key
    fireEvent.keyDown(brandLogo, { key: 'Enter' });
    expect(onTabChange).toHaveBeenCalledWith('stream');
    expect(onSelectTab).toHaveBeenCalledWith('console');

    // Test Space key
    onTabChange.mockClear();
    onSelectTab.mockClear();
    fireEvent.keyDown(brandLogo, { key: ' ' });
    expect(onTabChange).toHaveBeenCalledWith('stream');
    expect(onSelectTab).toHaveBeenCalledWith('console');
  });

  it('renders all four navigation tabs and switches tabs on click', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });

    const onTabChange = vi.fn();

    render(
      <Navbar
        activeTab="stream"
        onTabChange={onTabChange}
      />
    );

    await waitFor(() => {
      expect(systemApi.fetchHealth).toHaveBeenCalled();
    });

    const consoleBtn = screen.getByRole('button', { name: /Console View/i });
    const alertsBtn = screen.getByRole('button', { name: /Alerts & History/i });
    const storageBtn = screen.getByRole('button', { name: /Storage/i });
    const settingsBtn = screen.getByRole('button', { name: /^Settings$/i });

    expect(consoleBtn).toBeInTheDocument();
    expect(alertsBtn).toBeInTheDocument();
    expect(storageBtn).toBeInTheDocument();
    expect(settingsBtn).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Host Aliases/i })).toBeNull();

    fireEvent.click(alertsBtn);
    expect(onTabChange).toHaveBeenCalledWith('alerts');

    fireEvent.click(storageBtn);
    expect(onTabChange).toHaveBeenCalledWith('storage');

    fireEvent.click(settingsBtn);
    expect(onTabChange).toHaveBeenCalledWith('settings');

    fireEvent.click(consoleBtn);
    expect(onTabChange).toHaveBeenCalledWith('stream');
  });

  it('displays subtle App update available notice next to Logout when newer version exists', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });
    vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
      current_version: '1.1.0-beta.3',
      latest_version: '1.2.0',
      update_available: true,
      check_enabled: true,
      checked_at: 1700000000.0,
    });

    render(
      <Navbar
        activeTab="stream"
        onTabChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('App update available')).toBeInTheDocument();
    });

    const updateLink = screen.getByRole('link', { name: /App update available/i });
    expect(updateLink).toHaveAttribute('href', 'https://github.com/logshed/logshed/releases');
    expect(updateLink).toHaveAttribute('target', '_blank');
  });

  it('does not display App update available notice when check_enabled is false', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });
    vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
      current_version: '1.1.0-beta.3',
      latest_version: '1.2.0',
      update_available: true,
      check_enabled: false,
      checked_at: 1700000000.0,
    });

    render(
      <Navbar
        activeTab="stream"
        onTabChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(systemApi.fetchHealth).toHaveBeenCalled();
    });

    expect(screen.queryByText('App update available')).not.toBeInTheDocument();
  });

  it('renders amber maintenance banner when maintenance window is active', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({
      active: true,
      until: '2026-09-26T14:30:00Z',
    });

    render(
      <Navbar
        activeTab="stream"
        onTabChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Maintenance window active - alerts silenced until/i)).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /Clear/i })).toBeInTheDocument();
  });

  it('clears maintenance window when Clear button in banner is clicked', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({
      active: true,
      until: '2026-09-26T14:30:00Z',
    });
    const setMaintSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue({
      active: false,
      until: null,
    });

    render(
      <Navbar
        activeTab="stream"
        onTabChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Maintenance window active - alerts silenced until/i)).toBeInTheDocument();
    });

    const clearBtn = screen.getByRole('button', { name: /Clear/i });
    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(setMaintSpy).toHaveBeenCalledWith(null);
      expect(screen.queryByText(/Maintenance window active - alerts silenced until/i)).not.toBeInTheDocument();
    });
  });

  it('does not render maintenance banner when maintenance window is inactive', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({
      active: false,
      until: null,
    });

    render(
      <Navbar
        activeTab="stream"
        onTabChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(alertsApi.fetchMaintenanceWindow).toHaveBeenCalled();
    });

    expect(screen.queryByText(/Maintenance window active/i)).not.toBeInTheDocument();
  });
});


