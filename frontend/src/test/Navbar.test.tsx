import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { Navbar, getMaintenanceBannerText } from '../components/common/Navbar.tsx';
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

    render(
      <Navbar
        activeTab="settings"
        onTabChange={onTabChange}
      />
    );

    await waitFor(() => {
      expect(systemApi.fetchHealth).toHaveBeenCalled();
    });

    const brandLogo = screen.getByTitle('Go to Console View');
    fireEvent.click(brandLogo);

    expect(onTabChange).toHaveBeenCalledWith('stream');
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

    render(
      <Navbar
        activeTab="settings"
        onTabChange={onTabChange}
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

    // Test Space key
    onTabChange.mockClear();
    fireEvent.keyDown(brandLogo, { key: ' ' });
    expect(onTabChange).toHaveBeenCalledWith('stream');
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
    const rulesBtn = screen.getByRole('button', { name: /Rules & History/i });
    const storageBtn = screen.getByRole('button', { name: /Storage/i });
    const settingsBtn = screen.getByRole('button', { name: /^Settings$/i });

    expect(consoleBtn).toBeInTheDocument();
    expect(rulesBtn).toBeInTheDocument();
    expect(storageBtn).toBeInTheDocument();
    expect(settingsBtn).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Host Aliases/i })).toBeNull();

    fireEvent.click(rulesBtn);
    expect(onTabChange).toHaveBeenCalledWith('rules');

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

  it('renders repository migration notice banner when repo_deprecated is true', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      db: 'ok',
      queue_depth: 0,
      dropped_logs: 0,
      ingest_rate: 0.0,
    });
    vi.spyOn(systemApi, 'fetchVersion').mockResolvedValue({
      current_version: '1.2.0',
      latest_version: '1.2.0',
      update_available: false,
      check_enabled: true,
      checked_at: 1700000000.0,
      repo_deprecated: true,
    });

    render(
      <Navbar
        activeTab="stream"
        onTabChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Repository Moved:')).toBeInTheDocument();
    });

    expect(screen.getByText(/ghcr\.io\/benhornertech\/logshed/i)).toBeInTheDocument();
    expect(screen.getByText(/ghcr\.io\/logshed\/logshed/i)).toBeInTheDocument();

    const dismissBtn = screen.getByRole('button', { name: /Dismiss/i });
    fireEvent.click(dismissBtn);
    expect(screen.queryByText('Repository Moved:')).not.toBeInTheDocument();
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
      on_demand_until: '2026-09-26T14:30:00Z',
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
      on_demand_until: '2026-09-26T14:30:00Z',
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

  it('renders multi-session maintenance banner with session count and drop errors text', async () => {
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
      active_sessions_count: 3,
      log_handling: 'drop_errors',
      sessions: [
        {
          session_id: 's-1',
          initiated_by: 'api_token: Backup Runner',
          reason: 'Nightly backup',
          log_handling: 'drop_errors',
          target_app: null,
          target_host: null,
          expires_at: '2026-09-26T14:30:00Z',
        },
      ],
    });

    render(
      <Navbar
        activeTab="stream"
        onTabChange={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Maintenance active \(3 sessions\) - errors filtered until/i)).toBeInTheDocument();
    });
  });

  it('correctly constructs maintenance banner text via getMaintenanceBannerText', () => {
    // Single session via API
    const singleApi = getMaintenanceBannerText({
      active: true,
      until: '2026-09-26T14:30:00Z',
      active_sessions_count: 1,
      log_handling: 'silence_alerts',
      sessions: [
        {
          session_id: 's-1',
          initiated_by: 'api_token: Home Assistant',
          reason: 'Snapshot',
          log_handling: 'silence_alerts',
          target_app: null,
          target_host: null,
          expires_at: '2026-09-26T14:30:00Z',
        },
      ],
    });
    expect(singleApi).toContain('Maintenance active (API: Home Assistant) - alerts silenced until');

    // Multiple sessions
    const multi = getMaintenanceBannerText({
      active: true,
      until: '2026-09-26T14:30:00Z',
      active_sessions_count: 2,
      log_handling: 'drop_errors',
      sessions: [],
    });
    expect(multi).toContain('Maintenance active (2 sessions) - errors filtered until');
  });
});



describe('Navbar maintenance and health handling (B11)', () => {
  const okHealth = { status: 'ok', db: 'ok', queue_depth: 0, dropped_logs: 0, ingest_rate: 0 };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a Scheduled window link instead of Clear for schedule-only windows', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue(okHealth);
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({
      active: true,
      until: '2026-09-26T14:30:00Z',
      schedule_name: 'Nightly',
      sessions: [],
    });
    render(<Navbar activeTab="stream" onTabChange={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/Maintenance window active/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /Clear/i })).toBeNull();
    const onTabChange = vi.fn();
    cleanup();
    render(<Navbar activeTab="stream" onTabChange={onTabChange} />);
    const link = await screen.findByRole('link', { name: /Scheduled window/i });
    expect(link).toHaveAttribute('href', '/rules/maintenance');
    const popSpy = vi.fn();
    window.addEventListener('popstate', popSpy);
    const notPrevented = fireEvent.click(link);
    window.removeEventListener('popstate', popSpy);
    expect(notPrevented).toBe(false);
    expect(onTabChange).toHaveBeenCalledWith('rules');
    expect(window.location.pathname).toBe('/rules/maintenance');
    expect(popSpy).toHaveBeenCalled();
    window.history.replaceState(null, '', '/');
  });

  it('makes exactly one request when Clear is clicked', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue(okHealth);
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({
      active: true,
      until: '2026-09-26T14:30:00Z',
      on_demand_until: '2026-09-26T14:30:00Z',
    });
    const setSpy = vi.spyOn(alertsApi, 'setMaintenanceWindow').mockResolvedValue({ active: false, until: null });
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    render(<Navbar activeTab="stream" onTabChange={vi.fn()} />);
    const btn = await screen.findByRole('button', { name: /Clear/i });
    fetchSpy.mockClear();
    fireEvent.click(btn);
    await waitFor(() => expect(setSpy).toHaveBeenCalledTimes(1));
    const disableCalls = fetchSpy.mock.calls.filter((c) => String(c[0]).includes('/maintenance/disable'));
    expect(disableCalls).toHaveLength(0);
  });

  it.each([
    ['web_ui', '(Web UI)'],
    ['admin: ben', '(Web UI)'],
    ['mcp', '(MCP)'],
    ['MCP client', '(MCP)'],
    ['api_token: Backup', '(API: Backup)'],
  ])('maps initiated_by %s to %s in the banner', (initiatedBy, expected) => {
    const text = getMaintenanceBannerText({
      active: true,
      until: '2026-09-26T14:30:00Z',
      active_sessions_count: 1,
      sessions: [
        {
          session_id: 's',
          initiated_by: initiatedBy,
          reason: '',
          log_handling: 'alerts_only',
          target_app: null,
          target_host: null,
          expires_at: '2026-09-26T14:30:00Z',
        },
      ],
    } as any);
    expect(text).toContain(expected);
    expect(text).not.toContain('API: web_ui');
    expect(text).not.toContain('API: mcp');
  });

  it('shows a degraded indicator when the health check throws', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockRejectedValue(new Error('Service Unavailable'));
    vi.spyOn(alertsApi, 'fetchMaintenanceWindow').mockResolvedValue({ active: false, until: null });
    render(<Navbar activeTab="stream" onTabChange={vi.fn()} />);
    expect(await screen.findByTestId('health-degraded')).toBeInTheDocument();
  });
});
