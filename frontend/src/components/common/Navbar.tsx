import React, { useEffect, useRef, useState } from 'react';
import { Settings, LogOut, Radio, Database, ArrowUpCircle, Bell, AlertTriangle, ExternalLink } from 'lucide-react';
import { LogShedLogo } from './LogShedLogo.tsx';
import { useAuth } from '../../context/AuthContext.tsx';
import { fetchHealth, fetchVersion } from '../../api/system.ts';
import { fetchMaintenanceWindow } from '../../api/alerts.ts';
import { HealthResponse, AppTab, VersionInfo, MaintenanceWindowResponse } from '../../types.ts';
import { useMediaQuery, usePolling, useServerTimezone } from '../../utils/hooks.ts';
import { PullTouchHandlers } from '../../utils/usePullToRefresh.ts';
import { formatMaintenanceTime } from '../../utils/formatters.ts';

export const formatInitiator = (initiatedBy: string): string => {
  if (!initiatedBy) return '';
  if (initiatedBy === 'web_ui' || initiatedBy.startsWith('admin: ') || initiatedBy === 'admin') {
    return ' (Web UI)';
  }
  if (initiatedBy === 'mcp' || initiatedBy.startsWith('MCP') || initiatedBy.startsWith('mcp: ')) {
    return ' (MCP)';
  }
  if (initiatedBy.startsWith('api_token: ')) {
    return ` (API: ${initiatedBy.slice('api_token: '.length)})`;
  }
  return ` (${initiatedBy})`;
};

export const getMaintenanceBannerText = (maintenance: MaintenanceWindowResponse, timeZone?: string): string => {
  const untilFormatted = formatMaintenanceTime(maintenance.until, timeZone);
  const isDropErrors = maintenance.log_handling === 'drop_errors';
  const actionText = isDropErrors ? 'errors filtered' : 'alerts silenced';

  const sessions = maintenance.sessions || [];
  const activeCount = maintenance.active_sessions_count ?? sessions.length;

  if (activeCount === 1) {
    const singleSession = sessions[0];
    const label = formatInitiator(singleSession?.initiated_by || '');
    return `Maintenance active${label} - ${actionText} until ${untilFormatted}`;
  }

  if (activeCount > 1) {
    return `Maintenance active (${activeCount} sessions) - ${actionText} until ${untilFormatted}`;
  }

  return `Maintenance window active - alerts silenced until ${untilFormatted}`;
};

// The health dot turns amber once, on every poll for NEAR_LIMIT_DURATION_MS, the 95th percentile loop lag
// is at or above LOOP_LAG_WARNING_MS, or the ingestion queue holds at least QUEUE_WARNING_DEPTH logs (half
// of the server's 10,000) or Lost has grown since the previous poll
export const LOOP_LAG_WARNING_MS = 50;
export const QUEUE_WARNING_DEPTH = 5000;
export const NEAR_LIMIT_DURATION_MS = 30000;

export type NearLimitReason = 'loop' | 'queue' | 'both';

export const getNearLimitTooltip = (reason: NearLimitReason): string => {
  const seconds = NEAR_LIMIT_DURATION_MS / 1000;
  const loop = `its event loop has been running at least ${LOOP_LAG_WARNING_MS} ms late (95th percentile) for ${seconds} seconds, so syslog may be read late and UDP messages lost`;
  const queue = `its ingestion queue has held at least ${QUEUE_WARNING_DEPTH.toLocaleString('en-US')} logs, or lost logs because it was full, for ${seconds} seconds, so logs arrive faster than they can be stored`;
  const cause = reason === 'loop' ? loop : reason === 'queue' ? queue : `${loop}, and ${queue}`;
  return `LogShed is near its processing limit: ${cause}. Reduce the log volume or the number of rules, drop noisy logs with drop rules, or send over TCP.`;
};

export const getRateTooltip = (health: HealthResponse): string => {
  if (typeof health.loop_lag_p95_ms !== 'number') return 'Ingestion Rate';
  return (
    `Ingestion Rate\nEvent loop lag (last 60 s): ${health.loop_lag_ms ?? 0} ms now, ` +
    `${health.loop_lag_p95_ms} ms 95th percentile, ${health.loop_lag_max_ms ?? 0} ms max\n` +
    `Loop lag is how late LogShed gets to its work. Healthy: a few ms at the 95th percentile ` +
    `(short spikes in the max are normal); ${LOOP_LAG_WARNING_MS} ms or more for ` +
    `${NEAR_LIMIT_DURATION_MS / 1000} s turns on the amber dot.`
  );
};

interface NavbarProps {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  pullTouchHandlers?: PullTouchHandlers;
}

export const Navbar: React.FC<NavbarProps> = ({
  activeTab,
  onTabChange,
  pullTouchHandlers,
}) => {
  const { logout } = useAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [nearLimit, setNearLimit] = useState<NearLimitReason | null>(null);
  const lagHighSince = useRef<number | null>(null);
  const queueHighSince = useRef<number | null>(null);
  const lastDroppedLogs = useRef<number | null>(null);
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);
  const [maintenance, setMaintenance] = useState<MaintenanceWindowResponse | null>(null);
  const [migrationBannerDismissed, setMigrationBannerDismissed] = useState(false);
  const isMobile = useMediaQuery('(max-width: 767px)');
  const serverTimezone = useServerTimezone();

  const loadHealth = async () => {
    try {
      const res = await fetchHealth();
      setHealth(res);
      const now = Date.now();
      const p95 = res.loop_lag_p95_ms;
      const lagHigh = typeof p95 === 'number' && p95 >= LOOP_LAG_WARNING_MS;
      const dropped = res.dropped_logs ?? 0;
      const losing = lastDroppedLogs.current !== null && dropped > lastDroppedLogs.current;
      lastDroppedLogs.current = dropped;
      const queueHigh = (res.queue_depth ?? 0) >= QUEUE_WARNING_DEPTH || losing;
      lagHighSince.current = lagHigh ? (lagHighSince.current ?? now) : null;
      queueHighSince.current = queueHigh ? (queueHighSince.current ?? now) : null;
      const held = (since: number | null) => since !== null && now - since >= NEAR_LIMIT_DURATION_MS;
      const lagHeld = held(lagHighSince.current);
      const queueHeld = held(queueHighSince.current);
      setNearLimit(lagHeld && queueHeld ? 'both' : lagHeld ? 'loop' : queueHeld ? 'queue' : null);
    } catch {
      setHealth({ status: 'degraded' });
      lagHighSince.current = null;
      queueHighSince.current = null;
      lastDroppedLogs.current = null;
      setNearLimit(null);
    }
  };

  const loadMaintenance = async () => {
    try {
      const res = await fetchMaintenanceWindow();
      setMaintenance(res);
      window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));
    } catch {
      // Maintenance fetch fails silently
    }
  };

  useEffect(() => {
    loadHealth();
    loadMaintenance();

    const onMaintenanceUpdated = (e: Event) => {
      const customEvent = e as CustomEvent<{ active: boolean; until: string | null }>;
      if (customEvent.detail) {
        setMaintenance(customEvent.detail);
      } else {
        loadMaintenance();
      }
    };
    window.addEventListener('maintenance-updated', onMaintenanceUpdated);

    const loadVersion = async () => {
      try {
        const v = await fetchVersion();
        setVersionInfo(v);
      } catch {
        // Version check fails silently
      }
    };
    loadVersion();

    return () => {
      window.removeEventListener('maintenance-updated', onMaintenanceUpdated);
    };
  }, []);

  // The health dot and the maintenance banner (which AlertsPanel also follows through the event)
  usePolling(() => {
    loadHealth();
    loadMaintenance();
  }, 10000);

  const handleLogoClick = () => {
    onTabChange('stream');
  };

  return (
    <>
      {/* Top Fixed Header */}
      <header
        {...(isMobile && pullTouchHandlers ? pullTouchHandlers : {})}
        className="bg-canvas border-b border-line px-4 py-2 flex items-center justify-between select-none shrink-0 z-30 touch-none"
      >
        {/* Brand & Status Indicator */}
        <div className="flex items-center gap-x-4">
          <div
            onClick={handleLogoClick}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleLogoClick();
              }
            }}
            role="button"
            tabIndex={0}
            className="flex items-center gap-2.5 cursor-pointer hover:opacity-80 transition-opacity focus:outline-hidden focus-visible:ring-1 focus-visible:ring-accent-500 rounded-sm"
            title="Go to Console View"
          >
            <LogShedLogo className="w-6 h-6 text-accent-600" />
            {/* The logshed.dev wordmark; 1px lower than centre looks centred next to the icon */}
            <span className="relative top-px font-bold tracking-[-0.02em] text-[1.05rem] leading-6 text-fg-strong">LogShed</span>
          </div>

          {/* Ingestion & Queue Health */}
          {health && (
            <div className="flex items-center gap-x-2.5 sm:gap-x-3 text-xs font-mono text-fg-muted border-l border-line pl-2.5 sm:pl-4">
              {health.status !== 'ok' ? (
                <span
                  role="img"
                  aria-label="Health degraded"
                  title="Health check degraded or unreachable"
                  data-testid="health-degraded"
                  className="inline-block w-2 h-2 rounded-full bg-warning-400"
                />
              ) : nearLimit && (
                <span
                  role="img"
                  aria-label="Near processing limit"
                  title={getNearLimitTooltip(nearLimit)}
                  data-testid="health-near-limit"
                  className="inline-block w-2 h-2 rounded-full bg-warning-400"
                />
              )}
              <span title={getRateTooltip(health)} className="text-[11px] sm:text-xs whitespace-nowrap">
                <span className="hidden sm:inline">Rate:{' '}</span>
                <span className="text-fg">
                  {(health.ingest_rate ?? 0).toFixed(1)} logs/s
                </span>
              </span>
              <span title="Queue Depth" className="hidden sm:inline">
                Queue: <span className="text-fg">{health.queue_depth ?? 0}</span>
              </span>
              {(health.dropped_logs ?? 0) > 0 && (
                <span
                  title="Logs LogShed could not queue because its ingestion queue was full, since it started (resets on restart). Drop rules are counted separately."
                  className="text-[11px] sm:text-xs whitespace-nowrap"
                  data-testid="queue-lost"
                >
                  Lost: <span className="text-warning-400 font-bold">{health.dropped_logs}</span>
                </span>
              )}
              {typeof health.udp_receive_errors === 'number' && health.udp_receive_errors > 0 && (
                <span
                  title="Syslog UDP messages the operating system discarded because LogShed's receive buffer was full, since LogShed started (resets on restart). Not included in Lost. TCP syslog avoids this."
                  className="text-[11px] sm:text-xs whitespace-nowrap"
                  data-testid="udp-lost"
                >
                  UDP lost: <span className="text-warning-400 font-bold">{health.udp_receive_errors}</span>
                </span>
              )}
            </div>
          )}
        </div>

        {/* Desktop Center Navigation Tabs (hidden on mobile) */}
        {!isMobile && (
          <nav className="flex items-center gap-x-1 bg-surface p-1 rounded-lg border border-line">
            <button
              onClick={() => onTabChange('stream')}
              className={`flex items-center gap-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
                activeTab === 'stream'
                  ? 'bg-raised-strong text-fg-strong shadow-xs'
                  : 'text-fg-muted hover:text-fg hover:bg-raised'
              }`}
            >
              <Radio className="w-3.5 h-3.5" />
              <span>Console View</span>
            </button>

            <button
              onClick={() => onTabChange('rules')}
              className={`flex items-center gap-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
                activeTab === 'rules'
                  ? 'bg-raised-strong text-fg-strong shadow-xs'
                  : 'text-fg-muted hover:text-fg hover:bg-raised'
              }`}
            >
              <Bell className="w-3.5 h-3.5" />
              <span>Rules & History</span>
            </button>

            <button
              onClick={() => onTabChange('storage')}
              className={`flex items-center gap-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
                activeTab === 'storage'
                  ? 'bg-raised-strong text-fg-strong shadow-xs'
                  : 'text-fg-muted hover:text-fg hover:bg-raised'
              }`}
            >
              <Database className="w-3.5 h-3.5" />
              <span>Storage</span>
            </button>

            <button
              onClick={() => onTabChange('settings')}
              className={`flex items-center gap-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
                activeTab === 'settings'
                  ? 'bg-raised-strong text-fg-strong shadow-xs'
                  : 'text-fg-muted hover:text-fg hover:bg-raised'
              }`}
            >
              <Settings className="w-3.5 h-3.5" />
              <span>Settings</span>
            </button>
          </nav>
        )}

        {/* Right Controls */}
        <div className="flex items-center gap-x-2">
          {versionInfo?.update_available && versionInfo?.check_enabled !== false && (
            // Below sm only the icon and the version show (below 360 px the icon alone), so the badge stays
            // on one line beside the wordmark and the Rate figure; the full text stays in its name and
            // tooltip. An invisible layer 10 px above and below makes the tap target 44 px tall on a phone
            // without making the badge or the header taller.
            <a
              href={`https://github.com/logshed/logshed/releases/tag/v${versionInfo.latest_version}`}
              target="_blank"
              rel="noopener noreferrer"
              className="relative flex items-center gap-x-1 px-2 py-0.5 max-sm:after:absolute max-sm:after:inset-x-0 max-sm:after:-inset-y-2.5 max-sm:after:content-[''] text-xs font-medium whitespace-nowrap text-warning-400 bg-warning-950/40 hover:bg-warning-900/50 border border-warning-800/60 rounded-sm transition cursor-pointer"
              title={`App update available: v${versionInfo.latest_version}`}
              aria-label={`App update available: v${versionInfo.latest_version}`}
            >
              <ArrowUpCircle className="w-3.5 h-3.5 text-warning-400 shrink-0" />
              <span className="sm:hidden max-[359px]:hidden font-mono">v{versionInfo.latest_version}</span>
              <span className="hidden sm:inline">App update available</span>
            </a>
          )}
          <button
            onClick={logout}
            title="Sign Out"
            className="flex items-center gap-x-1 px-2.5 py-1 text-xs font-medium text-fg-muted hover:text-danger-300 hover:bg-danger-950/30 rounded-sm border border-transparent hover:border-danger-900 transition"
          >
            <LogOut className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Logout</span>
          </button>
        </div>
      </header>

      {/* Repository Migration Warning Banner */}
      {versionInfo?.repo_deprecated && !migrationBannerDismissed && (
        <div className="bg-warning-950/90 border-b border-warning-800 text-warning-200 px-4 py-2 text-xs flex items-center justify-between select-none shrink-0 z-20">
          <div className="flex items-center gap-x-2">
            <AlertTriangle className="w-4 h-4 text-warning-400 shrink-0" />
            <span>
              <strong>Repository Moved:</strong> You are running an image from <code className="font-mono bg-canvas px-1 py-0.5 rounded-sm text-warning-300">ghcr.io/benhornertech/logshed</code>. Please update your Docker Compose or Unraid template to <code className="font-mono bg-canvas px-1 py-0.5 rounded-sm text-warning-300">ghcr.io/logshed/logshed</code> to receive future updates.
            </span>
          </div>
          <div className="flex items-center gap-x-3 ml-4 shrink-0">
            <a
              href="https://github.com/logshed/logshed/releases"
              target="_blank"
              rel="noopener noreferrer"
              className="text-warning-300 hover:text-fg-max underline font-medium text-xs inline-flex items-center gap-1 cursor-pointer"
            >
              <span>Release Notes</span>
              <ExternalLink className="w-3 h-3" />
            </a>
            <button
              type="button"
              onClick={() => setMigrationBannerDismissed(true)}
              className="text-warning-400 hover:text-warning-200 cursor-pointer font-medium text-xs"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Maintenance Window Amber Banner */}
      {maintenance?.active && maintenance.until && (
        <div className="bg-warning-950/80 border-b border-warning-800/80 text-warning-200 px-4 py-1.5 text-xs flex items-center justify-between select-none shrink-0 z-20">
          <div className="flex items-center gap-x-2">
            <AlertTriangle className="w-3.5 h-3.5 text-warning-400 shrink-0" />
            <span>
              {getMaintenanceBannerText(maintenance, serverTimezone)}
            </span>
          </div>
          <a
            href="/rules/maintenance"
            onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
              e.preventDefault();
              onTabChange('rules');
              if (window.location.pathname !== '/rules/maintenance') {
                window.history.pushState(null, '', '/rules/maintenance');
              }
              window.dispatchEvent(new PopStateEvent('popstate'));
            }}
            className="text-warning-300 hover:text-fg-max underline cursor-pointer font-medium text-xs ml-4"
          >
            {maintenance.on_demand_until || (maintenance.sessions?.length ?? 0) > 0 ? 'Manage' : 'Scheduled window'}
          </a>
        </div>
      )}

      {/* Mobile Fixed Bottom Navigation Bar */}
      {isMobile && (
        <nav
          aria-label="Mobile navigation"
          className="grid grid-cols-4 fixed bottom-0 inset-x-0 bg-canvas/95 backdrop-blur-md border-t border-line z-40 px-1 py-1 select-none"
        >
          <button
            onClick={() => onTabChange('stream')}
            className={`flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-all ${
              activeTab === 'stream'
                ? 'text-accent-400 font-semibold bg-raised/80'
                : 'text-fg-muted hover:text-fg'
            }`}
          >
            <Radio className="w-4 h-4 mb-0.5" />
            <span>Console</span>
          </button>

          <button
            onClick={() => onTabChange('rules')}
            className={`flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-all ${
              activeTab === 'rules'
                ? 'text-accent-400 font-semibold bg-raised/80'
                : 'text-fg-muted hover:text-fg'
            }`}
          >
            <Bell className="w-4 h-4 mb-0.5" />
            <span>Rules & History</span>
          </button>

          <button
            onClick={() => onTabChange('storage')}
            className={`flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-all ${
              activeTab === 'storage'
                ? 'text-accent-400 font-semibold bg-raised/80'
                : 'text-fg-muted hover:text-fg'
            }`}
          >
            <Database className="w-4 h-4 mb-0.5" />
            <span>Storage</span>
          </button>

          <button
            onClick={() => onTabChange('settings')}
            className={`flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-all ${
              activeTab === 'settings'
                ? 'text-accent-400 font-semibold bg-raised/80'
                : 'text-fg-muted hover:text-fg'
            }`}
          >
            <Settings className="w-4 h-4 mb-0.5" />
            <span>Settings</span>
          </button>
        </nav>
      )}
    </>
  );
};
