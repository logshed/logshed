import React, { useEffect, useState } from 'react';
import { Settings, LogOut, Radio, Database, ArrowUpCircle, Bell, AlertTriangle, ExternalLink } from 'lucide-react';
import { LogShedLogo } from './LogShedLogo.tsx';
import { useAuth } from '../../context/AuthContext.tsx';
import { fetchHealth, fetchVersion } from '../../api/system.ts';
import { fetchMaintenanceWindow, setMaintenanceWindow } from '../../api/alerts.ts';
import { HealthResponse, AppTab, VersionInfo } from '../../types.ts';
import { useMediaQuery } from '../../utils/hooks.ts';
import { PullTouchHandlers } from '../../utils/usePullToRefresh.ts';
import { formatMaintenanceTime } from '../../utils/formatters.ts';

interface NavbarProps {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  onSelectTab?: (tab: string) => void;
  isStreaming?: boolean;
  pullTouchHandlers?: PullTouchHandlers;
}

export const Navbar: React.FC<NavbarProps> = ({
  activeTab,
  onTabChange,
  onSelectTab,
  pullTouchHandlers,
}) => {
  const { logout } = useAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);
  const [maintenance, setMaintenance] = useState<{ active: boolean; until: string | null } | null>(null);
  const [migrationBannerDismissed, setMigrationBannerDismissed] = useState(false);
  const isMobile = useMediaQuery('(max-width: 767px)');

  const loadHealth = async () => {
    try {
      const res = await fetchHealth();
      setHealth(res);
    } catch {
      // Health fetch error handled gracefully
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
    const interval = setInterval(() => {
      loadHealth();
      loadMaintenance();
    }, 10000);

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
      clearInterval(interval);
      window.removeEventListener('maintenance-updated', onMaintenanceUpdated);
    };
  }, []);

  const handleClearMaintenance = async () => {
    try {
      const res = await setMaintenanceWindow(null);
      setMaintenance(res);
      window.dispatchEvent(new CustomEvent('maintenance-updated', { detail: res }));
    } catch {
      // Clear failure handled gracefully
    }
  };

  const handleLogoClick = () => {
    if (onSelectTab) {
      onSelectTab('console');
    }
    onTabChange('stream');
  };

  return (
    <>
      {/* Top Fixed Header */}
      <header
        {...(isMobile && pullTouchHandlers ? pullTouchHandlers : {})}
        className="bg-dark-950 border-b border-dark-700 px-4 py-2 flex items-center justify-between select-none shrink-0 z-30 touch-none"
      >
        {/* Brand & Status Indicator */}
        <div className="flex items-center space-x-4">
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
            className="flex items-center space-x-2 cursor-pointer hover:opacity-80 transition-opacity focus:outline-hidden focus:ring-1 focus:ring-accent-500 rounded"
            title="Go to Console View"
          >
            <LogShedLogo className="w-5 h-5 text-accent-500" />
            <span className="font-semibold tracking-wide text-slate-100 text-sm">
              LOG<span className="text-accent-500 font-mono">SHED</span>
            </span>
          </div>

          {/* Ingestion & Queue Health */}
          {health && (
            <div className="hidden sm:flex items-center space-x-3 text-xs font-mono text-slate-400 border-l border-dark-700 pl-4">
              <span title="Ingestion Rate">
                Rate:{' '}
                <span className="text-slate-200">
                  {(health.ingest_rate ?? 0).toFixed(1)} logs/s
                </span>
              </span>
              <span title="Queue Depth">
                Queue: <span className="text-slate-200">{health.queue_depth}</span>
              </span>
              <span title="Dropped Logs Total">
                Dropped:{' '}
                <span className={health.dropped_logs > 0 ? 'text-amber-400 font-bold' : 'text-slate-200'}>
                  {health.dropped_logs}
                </span>
              </span>
            </div>
          )}
        </div>

        {/* Desktop Center Navigation Tabs (hidden on mobile) */}
        {!isMobile && (
          <nav className="flex items-center space-x-1 bg-dark-900 p-1 rounded-lg border border-dark-700">
            <button
              onClick={() => onTabChange('stream')}
              className={`flex items-center space-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
                activeTab === 'stream'
                  ? 'bg-dark-700 text-slate-100 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-dark-800'
              }`}
            >
              <Radio className="w-3.5 h-3.5" />
              <span>Console View</span>
            </button>

            <button
              onClick={() => onTabChange('rules')}
              className={`flex items-center space-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
                activeTab === 'rules'
                  ? 'bg-dark-700 text-slate-100 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-dark-800'
              }`}
            >
              <Bell className="w-3.5 h-3.5" />
              <span>Rules & History</span>
            </button>

            <button
              onClick={() => onTabChange('storage')}
              className={`flex items-center space-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
                activeTab === 'storage'
                  ? 'bg-dark-700 text-slate-100 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-dark-800'
              }`}
            >
              <Database className="w-3.5 h-3.5" />
              <span>Storage</span>
            </button>

            <button
              onClick={() => onTabChange('settings')}
              className={`flex items-center space-x-1.5 px-3 py-1 text-xs font-medium rounded-md transition-all ${
                activeTab === 'settings'
                  ? 'bg-dark-700 text-slate-100 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-dark-800'
              }`}
            >
              <Settings className="w-3.5 h-3.5" />
              <span>Settings</span>
            </button>
          </nav>
        )}

        {/* Right Controls */}
        <div className="flex items-center space-x-2">
          {versionInfo?.update_available && versionInfo?.check_enabled !== false && (
            <a
              href="https://github.com/logshed/logshed/releases"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center space-x-1 px-2 py-0.5 text-xs font-medium text-amber-400 bg-amber-950/40 hover:bg-amber-900/50 border border-amber-800/60 rounded transition cursor-pointer"
              title={`App update available: v${versionInfo.latest_version}`}
            >
              <ArrowUpCircle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
              <span>App update available</span>
            </a>
          )}
          <button
            onClick={logout}
            title="Sign Out"
            className="flex items-center space-x-1 px-2.5 py-1 text-xs font-medium text-slate-400 hover:text-red-300 hover:bg-red-950/30 rounded border border-transparent hover:border-red-900 transition"
          >
            <LogOut className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Logout</span>
          </button>
        </div>
      </header>

      {/* Repository Migration Warning Banner */}
      {versionInfo?.repo_deprecated && !migrationBannerDismissed && (
        <div className="bg-amber-950/90 border-b border-amber-800 text-amber-200 px-4 py-2 text-xs flex items-center justify-between select-none shrink-0 z-20">
          <div className="flex items-center space-x-2">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
            <span>
              <strong>Repository Moved:</strong> You are running an image from <code className="font-mono bg-dark-950 px-1 py-0.5 rounded text-amber-300">ghcr.io/benhornertech/logshed</code>. Please update your Docker Compose or Unraid template to <code className="font-mono bg-dark-950 px-1 py-0.5 rounded text-amber-300">ghcr.io/logshed/logshed</code> to receive future updates.
            </span>
          </div>
          <div className="flex items-center space-x-3 ml-4 shrink-0">
            <a
              href="https://github.com/logshed/logshed/releases"
              target="_blank"
              rel="noopener noreferrer"
              className="text-amber-300 hover:text-white underline font-medium text-xs inline-flex items-center gap-1 cursor-pointer"
            >
              <span>Release Notes</span>
              <ExternalLink className="w-3 h-3" />
            </a>
            <button
              type="button"
              onClick={() => setMigrationBannerDismissed(true)}
              className="text-amber-400 hover:text-amber-200 cursor-pointer font-medium text-xs"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Maintenance Window Amber Banner */}
      {maintenance?.active && maintenance.until && (
        <div className="bg-amber-950/80 border-b border-amber-800/80 text-amber-200 px-4 py-1.5 text-xs flex items-center justify-between select-none shrink-0 z-20">
          <div className="flex items-center space-x-2">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
            <span>
              Maintenance window active - alerts silenced until {formatMaintenanceTime(maintenance.until)}
            </span>
          </div>
          <button
            type="button"
            onClick={handleClearMaintenance}
            className="text-amber-300 hover:text-white underline cursor-pointer font-medium text-xs ml-4"
          >
            Clear
          </button>
        </div>
      )}

      {/* Mobile Fixed Bottom Navigation Bar */}
      {isMobile && (
        <nav
          aria-label="Mobile navigation"
          className="grid grid-cols-4 fixed bottom-0 inset-x-0 bg-dark-950/95 backdrop-blur-md border-t border-dark-700 z-40 px-1 py-1 select-none"
        >
          <button
            onClick={() => onTabChange('stream')}
            className={`flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-all ${
              activeTab === 'stream'
                ? 'text-accent-400 font-semibold bg-dark-800/80'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Radio className="w-4 h-4 mb-0.5" />
            <span>Console</span>
          </button>

          <button
            onClick={() => onTabChange('rules')}
            className={`flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-all ${
              activeTab === 'rules'
                ? 'text-accent-400 font-semibold bg-dark-800/80'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Bell className="w-4 h-4 mb-0.5" />
            <span>Rules & History</span>
          </button>

          <button
            onClick={() => onTabChange('storage')}
            className={`flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-all ${
              activeTab === 'storage'
                ? 'text-accent-400 font-semibold bg-dark-800/80'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Database className="w-4 h-4 mb-0.5" />
            <span>Storage</span>
          </button>

          <button
            onClick={() => onTabChange('settings')}
            className={`flex flex-col items-center justify-center py-1.5 px-1 rounded-md text-[10px] font-medium transition-all ${
              activeTab === 'settings'
                ? 'text-accent-400 font-semibold bg-dark-800/80'
                : 'text-slate-400 hover:text-slate-200'
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
