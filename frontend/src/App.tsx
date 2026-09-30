import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { AlertCircle, RefreshCw, Save } from 'lucide-react';
import { useAuth } from './context/AuthContext.tsx';
import { AliasContext } from './context/AliasContext.tsx';
import { Navbar } from './components/common/Navbar.tsx';
import { Modal } from './components/common/Modal.tsx';
import { LoginForm } from './components/auth/LoginForm.tsx';
import { SetupModal } from './components/auth/SetupModal.tsx';
import { LiveLogStream } from './components/logs/LiveLogStream.tsx';
import { StoragePanel } from './components/storage/StoragePanel.tsx';
import { AlertsPanel } from './components/alerts/AlertsPanel.tsx';
import { SettingsPanel } from './components/settings/SettingsPanel.tsx';
import { AiAnalysisModal } from './components/ai/AiAnalysisModal.tsx';
import { LogEntry, AppTab, SettingsSubTab } from './types.ts';
import { LogShedLogo } from './components/common/LogShedLogo.tsx';
import { useMediaQuery } from './utils/hooks.ts';
import { usePullToRefresh } from './utils/usePullToRefresh.ts';
import { fetchVersion } from './api/system.ts';

export const pathToTab = (pathname: string): AppTab => {
  const clean = pathname.replace(/\/+$/, '').toLowerCase();
  if (clean === '/aliases' || clean.startsWith('/aliases/')) return 'settings';
  if (clean === '/storage' || clean.startsWith('/storage/')) return 'storage';
  if (clean === '/rules' || clean.startsWith('/rules/')) return 'rules';
  if (clean === '/settings' || clean.startsWith('/settings/')) return 'settings';
  return 'stream';
};

export const tabToPath = (tab: AppTab): string => {
  switch (tab) {
    case 'storage':
      return '/storage';
    case 'rules':
      return '/rules';
    case 'settings':
      return '/settings';
    case 'stream':
    default:
      return '/';
  }
};

export const App: React.FC = () => {
  const { isAuthenticated, setupRequired, isLoading } = useAuth();
  const [activeTab, setActiveTab] = useState<AppTab>(() => pathToTab(window.location.pathname));
  const [aiSelectedLogs, setAiSelectedLogs] = useState<LogEntry[]>([]);
  const [addAliasIp, setAddAliasIp] = useState<string | null>(null);
  const [pendingSettingsSubTab, setPendingSettingsSubTab] = useState<SettingsSubTab | null>(null);
  const [clearSelectionSignal, setClearSelectionSignal] = useState<number>(0);
  const [aliasVersion, setAliasVersion] = useState<number>(0);
  const bumpAliasVersion = useCallback(() => {
    setAliasVersion((prev) => prev + 1);
  }, []);
  const aliasContextValue = useMemo(
    () => ({ aliasVersion, bumpAliasVersion }),
    [aliasVersion, bumpAliasVersion]
  );
  const isMobile = useMediaQuery('(max-width: 767px)');

  const {
    pullDistance,
    isPulling,
    hasReachedThreshold,
    isRefreshing,
    touchHandlers: pullTouchHandlers,
  } = usePullToRefresh({
    onRefresh: () => {
      window.location.reload();
    },
    threshold: 55,
    disabled: !isMobile,
  });

  // Settings unsaved changes guard
  const [isSettingsDirty, setIsSettingsDirty] = useState<boolean>(false);
  const [pendingTab, setPendingTab] = useState<AppTab | null>(null);
  const [isSavingModal, setIsSavingModal] = useState<boolean>(false);
  const saveSettingsTriggerRef = useRef<(() => Promise<boolean>) | null>(null);

  // Synchronize browser history and popstate navigation
  useEffect(() => {
    const handlePopState = () => {
      const nextTab = pathToTab(window.location.pathname);
      if (activeTab === 'settings' && isSettingsDirty && nextTab !== 'settings') {
        window.history.pushState(null, '', tabToPath(activeTab));
        setPendingTab(nextTab);
        return;
      }
      setActiveTab(nextTab);
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [activeTab, isSettingsDirty]);

  // Dynamic browser title for beta / prerelease builds (e.g. LogShed [1.2.0-beta.1])
  const titleSetRef = useRef<boolean>(false);
  useEffect(() => {
    if (!isAuthenticated || titleSetRef.current) return;
    titleSetRef.current = true;

    fetchVersion()
      .then((data) => {
        if (data.current_version && data.current_version.includes('-')) {
          document.title = `LogShed [${data.current_version}]`;
        } else {
          document.title = 'LogShed';
        }
      })
      .catch(() => {
        // Fall back to default title on error
      });
  }, [isAuthenticated]);

  if (isLoading) {
    return (
      <div className="min-h-screen bg-dark-950 flex flex-col items-center justify-center text-slate-400 font-mono text-xs space-y-3">
        <LogShedLogo className="w-8 h-8 text-accent-500 animate-pulse" />
        <span>Loading LogShed...</span>
      </div>
    );
  }

  if (setupRequired) {
    return <SetupModal />;
  }

  if (!isAuthenticated) {
    return <LoginForm />;
  }

  const handleOpenAiModal = (logs: LogEntry[]) => {
    setAiSelectedLogs(logs);
  };

  const handleAddAliasFromLog = (ip: string) => {
    setAddAliasIp(ip);
    setPendingSettingsSubTab('aliases');
    handleTabChange('settings');
  };

  const handleTabChange = (nextTab: AppTab) => {
    if (activeTab === 'settings' && isSettingsDirty && nextTab !== 'settings') {
      setPendingTab(nextTab);
      return;
    }
    if (nextTab !== 'settings') {
      setPendingSettingsSubTab(null);
    }
    setActiveTab(nextTab);
    const targetPath = tabToPath(nextTab);
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
  };

  return (
    <AliasContext.Provider value={aliasContextValue}>
      <div className="h-dvh max-h-dvh w-full max-w-full bg-dark-950 text-slate-200 flex flex-col overflow-hidden select-text">
      {/* Mobile Pull-to-Refresh Indicator */}
      {isMobile && (isPulling || isRefreshing) && (
        <div
          style={{ transform: `translateY(${Math.min(pullDistance, 45)}px)` }}
          className="fixed top-0 inset-x-0 flex items-center justify-center pointer-events-none z-50 transition-transform duration-75"
        >
          <div className="bg-dark-900/95 border border-dark-600 rounded-full px-3.5 py-1 text-xs flex items-center gap-1.5 shadow-xl text-slate-200 backdrop-blur-sm">
            <RefreshCw
              className={`w-3.5 h-3.5 text-accent-400 ${
                isRefreshing || hasReachedThreshold ? 'animate-spin' : ''
              }`}
              style={{
                transform: isRefreshing ? undefined : `rotate(${pullDistance * 5}deg)`,
              }}
            />
            <span className="font-mono text-[11px]">
              {isRefreshing
                ? 'Refreshing...'
                : hasReachedThreshold
                ? 'Release to refresh'
                : 'Pull down to refresh'}
            </span>
          </div>
        </div>
      )}

      {/* Top Fixed Navbar & Mobile Bottom Navigation */}
      <Navbar
        activeTab={activeTab}
        onTabChange={handleTabChange}
        isStreaming={activeTab === 'stream'}
        pullTouchHandlers={pullTouchHandlers}
      />

      {/* Main Content Area */}
      <main
        className={`flex-1 min-h-0 relative ${
          activeTab === 'stream'
            ? 'overflow-hidden flex flex-col'
            : 'overflow-y-auto overflow-x-hidden [scrollbar-gutter:stable]'
        } pb-14 md:pb-0`}
      >
        {activeTab === 'stream' && (
          <LiveLogStream
            onDiagnoseAi={handleOpenAiModal}
            onAddAlias={handleAddAliasFromLog}
            clearSelectionSignal={clearSelectionSignal}
            pullTouchHandlers={pullTouchHandlers}
          />
        )}

        {activeTab === 'storage' && <StoragePanel />}

        {activeTab === 'rules' && (
          <AlertsPanel
            onNavigateToSettings={() => handleTabChange('settings')}
          />
        )}

        {activeTab === 'settings' && (
          <SettingsPanel
            initialSubTab={pendingSettingsSubTab || undefined}
            initialAddIp={addAliasIp}
            onAliasSaved={() => {
              setAddAliasIp(null);
              setPendingSettingsSubTab(null);
            }}
            onDirtyChange={setIsSettingsDirty}
            saveTriggerRef={saveSettingsTriggerRef}
          />
        )}
      </main>

      {/* Global AI Root-Cause Analysis Modal */}
      <AiAnalysisModal
        isOpen={aiSelectedLogs.length > 0}
        onClose={() => {
          setAiSelectedLogs([]);
          if (isMobile) {
            setClearSelectionSignal((prev) => prev + 1);
          }
        }}
        selectedLogs={aiSelectedLogs}
        onNavigateToSettings={() => handleTabChange('settings')}
      />

      {/* Unsaved Changes Confirmation Modal */}
      <Modal
        isOpen={pendingTab !== null}
        onClose={() => {
          setPendingTab(null);
          const currentPath = tabToPath(activeTab);
          if (window.location.pathname !== currentPath) {
            window.history.pushState(null, '', currentPath);
          }
        }}
        title="Unsaved Changes"
        maxWidth="max-w-md"
      >
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 shrink-0">
              <AlertCircle className="w-5 h-5" />
            </div>
            <p className="text-xs text-slate-300 leading-relaxed">
              You have unsaved changes in System Configuration. Leaving now will discard those edits.
            </p>
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-end gap-2 pt-3 border-t border-dark-700">
            <button
              type="button"
              onClick={() => {
                setPendingTab(null);
                const currentPath = tabToPath(activeTab);
                if (window.location.pathname !== currentPath) {
                  window.history.pushState(null, '', currentPath);
                }
              }}
              className="w-full sm:w-auto px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 hover:bg-dark-750 border border-dark-700 transition cursor-pointer"
            >
              Keep Editing
            </button>
            <button
              type="button"
              onClick={() => {
                const target = pendingTab;
                setPendingTab(null);
                setIsSettingsDirty(false);
                if (target) {
                  setActiveTab(target);
                  const targetPath = tabToPath(target);
                  if (window.location.pathname !== targetPath) {
                    window.history.pushState(null, '', targetPath);
                  }
                }
              }}
              className="w-full sm:w-auto px-3 py-1.5 rounded-lg text-xs font-medium text-red-400 hover:text-red-300 bg-red-950/40 hover:bg-red-900/40 border border-red-800/60 transition cursor-pointer"
            >
              Discard &amp; Leave
            </button>
            <button
              type="button"
              disabled={isSavingModal}
              onClick={async () => {
                setIsSavingModal(true);
                try {
                  const success = saveSettingsTriggerRef.current
                    ? await saveSettingsTriggerRef.current()
                    : true;
                  if (success) {
                    const target = pendingTab;
                    setPendingTab(null);
                    setIsSettingsDirty(false);
                    if (target) {
                      setActiveTab(target);
                      const targetPath = tabToPath(target);
                      if (window.location.pathname !== targetPath) {
                        window.history.pushState(null, '', targetPath);
                      }
                    }
                  }
                } finally {
                  setIsSavingModal(false);
                }
              }}
              className="w-full sm:w-auto px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 transition cursor-pointer flex items-center justify-center gap-1.5 disabled:opacity-50 shadow-sm"
            >
              {isSavingModal ? (
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              <span>Save &amp; Continue</span>
            </button>
          </div>
        </div>
      </Modal>
      </div>
    </AliasContext.Provider>
  );
};
