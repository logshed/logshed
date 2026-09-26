import React, { useState, useEffect, useRef } from 'react';
import {
  Sliders,
  Cpu,
  Bell,
  Box,
  Network,
  Server,
  Check,
  AlertCircle,
  RefreshCw,
  Save,
} from 'lucide-react';
import { SystemSettings } from '../../types.ts';
import { updateSettings } from '../../api/settings.ts';

export interface AdvancedSettingsCardProps {
  settings: SystemSettings | null;
  onSettingsSaved: () => Promise<void> | void;
  onDirtyChange?: (dirty: boolean) => void;
}

export const AdvancedSettingsCard: React.FC<AdvancedSettingsCardProps> = ({
  settings,
  onSettingsSaved,
  onDirtyChange,
}) => {
  // Form state
  const [aiTimeout, setAiTimeout] = useState<number>(45.0);
  const [aiThinkingBudget, setAiThinkingBudget] = useState<number>(1024);
  const [appUrl, setAppUrl] = useState<string>('');
  const [allowPrivate, setAllowPrivate] = useState<boolean>(true);
  const [enableDocker, setEnableDocker] = useState<boolean>(true);
  const [dockerExcludeContainers, setDockerExcludeContainers] = useState<string>('');
  const [dockerSourceAlias, setDockerSourceAlias] = useState<string>('docker');
  const [trustedProxies, setTrustedProxies] = useState<string>('');
  const [trustDockerProxies, setTrustDockerProxies] = useState<boolean>(false);
  const [cookieSecure, setCookieSecure] = useState<boolean>(false);
  const [syslogMaxTcpConnections, setSyslogMaxTcpConnections] = useState<number>(250);
  const [syslogTcpInactivityTimeout, setSyslogTcpInactivityTimeout] = useState<number>(0.0);

  // Status state
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveInlineError, setSaveInlineError] = useState<string | null>(null);
  const [saveInlineSuccess, setSaveInlineSuccess] = useState<boolean>(false);
  const saveSuccessTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (saveSuccessTimeoutRef.current) {
        clearTimeout(saveSuccessTimeoutRef.current);
      }
    };
  }, []);

  // Populate from settings
  useEffect(() => {
    if (settings) {
      setAiTimeout(settings.ai_timeout ?? 45.0);
      setAiThinkingBudget(settings.ai_thinking_budget ?? 1024);
      setAppUrl(settings.app_url ?? '');
      setAllowPrivate(settings.allow_private_notification_targets ?? true);
      setEnableDocker(settings.enable_docker ?? true);
      setDockerExcludeContainers(settings.docker_exclude_containers ?? '');
      setDockerSourceAlias(settings.docker_source_alias ?? 'docker');
      setTrustedProxies(settings.trusted_proxies ?? '');
      setTrustDockerProxies(settings.trust_docker_proxies ?? false);
      setCookieSecure(settings.cookie_secure ?? false);
      setSyslogMaxTcpConnections(settings.syslog_max_tcp_connections ?? 250);
      setSyslogTcpInactivityTimeout(settings.syslog_tcp_inactivity_timeout ?? 0.0);
    }
  }, [settings]);

  // Dirty state calculation
  const isDirty = Boolean(
    settings && (
      aiTimeout !== (settings.ai_timeout ?? 45.0) ||
      aiThinkingBudget !== (settings.ai_thinking_budget ?? 1024) ||
      appUrl !== (settings.app_url ?? '') ||
      allowPrivate !== (settings.allow_private_notification_targets ?? true) ||
      enableDocker !== (settings.enable_docker ?? true) ||
      dockerExcludeContainers !== (settings.docker_exclude_containers ?? '') ||
      dockerSourceAlias !== (settings.docker_source_alias ?? 'docker') ||
      trustedProxies !== (settings.trusted_proxies ?? '') ||
      trustDockerProxies !== (settings.trust_docker_proxies ?? false) ||
      cookieSecure !== (settings.cookie_secure ?? false) ||
      syslogMaxTcpConnections !== (settings.syslog_max_tcp_connections ?? 250) ||
      syslogTcpInactivityTimeout !== (settings.syslog_tcp_inactivity_timeout ?? 0.0)
    )
  );

  useEffect(() => {
    onDirtyChange?.(isDirty);
  }, [isDirty, onDirtyChange]);

  const handleReset = () => {
    if (!settings) return;
    setAiTimeout(settings.ai_timeout ?? 45.0);
    setAiThinkingBudget(settings.ai_thinking_budget ?? 1024);
    setAppUrl(settings.app_url ?? '');
    setAllowPrivate(settings.allow_private_notification_targets ?? true);
    setEnableDocker(settings.enable_docker ?? true);
    setDockerExcludeContainers(settings.docker_exclude_containers ?? '');
    setDockerSourceAlias(settings.docker_source_alias ?? 'docker');
    setTrustedProxies(settings.trusted_proxies ?? '');
    setTrustDockerProxies(settings.trust_docker_proxies ?? false);
    setCookieSecure(settings.cookie_secure ?? false);
    setSyslogMaxTcpConnections(settings.syslog_max_tcp_connections ?? 250);
    setSyslogTcpInactivityTimeout(settings.syslog_tcp_inactivity_timeout ?? 0.0);
    setSaveInlineError(null);
    setSaveInlineSuccess(false);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveInlineError(null);
    setSaveInlineSuccess(false);

    // Validation
    if (aiTimeout <= 0) {
      setSaveInlineError('Request Timeout must be greater than 0 seconds.');
      return;
    }
    if (aiThinkingBudget < 0) {
      setSaveInlineError('Thinking Token Budget cannot be negative.');
      return;
    }
    if (syslogMaxTcpConnections < 1) {
      setSaveInlineError('Max Concurrent TCP Connections must be at least 1.');
      return;
    }
    if (syslogTcpInactivityTimeout < 0) {
      setSaveInlineError('Client Inactivity Timeout cannot be negative.');
      return;
    }

    try {
      setIsSaving(true);
      await updateSettings({
        ai_timeout: aiTimeout,
        ai_thinking_budget: aiThinkingBudget,
        app_url: appUrl.trim(),
        allow_private_notification_targets: allowPrivate,
        enable_docker: enableDocker,
        docker_exclude_containers: dockerExcludeContainers.trim(),
        docker_source_alias: dockerSourceAlias.trim() || 'docker',
        trusted_proxies: trustedProxies.trim(),
        trust_docker_proxies: trustDockerProxies,
        cookie_secure: cookieSecure,
        syslog_max_tcp_connections: syslogMaxTcpConnections,
        syslog_tcp_inactivity_timeout: syslogTcpInactivityTimeout,
      });

      setSaveInlineSuccess(true);
      if (saveSuccessTimeoutRef.current) clearTimeout(saveSuccessTimeoutRef.current);
      saveSuccessTimeoutRef.current = setTimeout(() => {
        setSaveInlineSuccess(false);
      }, 3000);

      await onSettingsSaved();
    } catch (err: any) {
      setSaveInlineError(err.message || 'Failed to save advanced system settings.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-6">
      {/* Header */}
      <div>
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Sliders className="w-4 h-4 text-accent-500" />
          <span>Advanced System Settings</span>
        </h3>
        <p className="text-xs text-slate-400 mt-0.5">
          Configure internal collector limits, network proxy subnets, and AI execution boundaries.
        </p>
      </div>

      <form onSubmit={handleSave} className="space-y-6">
        {/* Section 1: AI Engine Limits */}
        <div className="space-y-3 pt-1 border-t border-dark-800">
          <h4 className="text-xs font-semibold text-slate-300 flex items-center gap-2">
            <Cpu className="w-3.5 h-3.5 text-accent-400" />
            <span>AI Engine Limits</span>
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label htmlFor="ai-timeout" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Request Timeout (seconds)
              </label>
              <input
                id="ai-timeout"
                type="number"
                min={5}
                step={5}
                value={aiTimeout}
                onChange={(e) => setAiTimeout(parseFloat(e.target.value) || 5)}
                required
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>

            <div>
              <label htmlFor="ai-thinking-budget" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Thinking Token Budget
              </label>
              <input
                id="ai-thinking-budget"
                type="number"
                min={0}
                max={32768}
                step={128}
                value={aiThinkingBudget}
                onChange={(e) => setAiThinkingBudget(parseInt(e.target.value, 10) || 0)}
                required
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>
          </div>
          <p className="text-[11px] text-slate-500">
            Controls timeout and reasoning tokens across Gemini and OpenAI models. Setting budget to 0 disables thinking tokens.
          </p>
        </div>

        {/* Section 2: Notifications & Webhooks */}
        <div className="space-y-3 pt-3 border-t border-dark-800">
          <h4 className="text-xs font-semibold text-slate-300 flex items-center gap-2">
            <Bell className="w-3.5 h-3.5 text-accent-400" />
            <span>Notifications & Webhooks</span>
          </h4>
          <div className="space-y-3">
            <div>
              <label htmlFor="app-url" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Public Instance URL
              </label>
              <input
                id="app-url"
                type="text"
                placeholder="https://logs.example.com"
                value={appUrl}
                onChange={(e) => setAppUrl(e.target.value)}
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
              <p className="text-[11px] text-slate-500 mt-1">
                Used in notification dispatch to generate direct links to incident history.
              </p>
            </div>

            <div className="pt-1">
              <label className="flex items-center gap-2.5 text-xs text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  aria-label="Allow Local / LAN Webhook Targets"
                  checked={allowPrivate}
                  onChange={(e) => setAllowPrivate(e.target.checked)}
                  className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
                />
                <span className="font-medium">Allow Local / LAN Webhook Targets</span>
              </label>
              <p className="text-[11px] text-slate-500 mt-1 pl-6.5">
                Permits webhooks targeting private IPv4/IPv6 ranges (e.g. local Gotify or Home Assistant).
              </p>
            </div>
          </div>
        </div>

        {/* Section 3: Docker Collector */}
        <div className="space-y-3 pt-3 border-t border-dark-800">
          <h4 className="text-xs font-semibold text-slate-300 flex items-center gap-2">
            <Box className="w-3.5 h-3.5 text-accent-400" />
            <span>Docker Collector</span>
          </h4>
          <div className="space-y-3">
            <div>
              <label className="flex items-center gap-2.5 text-xs text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  aria-label="Enable Container Tailing"
                  checked={enableDocker}
                  onChange={(e) => setEnableDocker(e.target.checked)}
                  className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
                />
                <span className="font-medium">Enable Container Tailing</span>
              </label>
              <p className="text-[11px] text-slate-500 mt-1 pl-6.5">
                Applies dynamically to active container discovery and log stream ingestion.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label htmlFor="docker-exclude-containers" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                  Excluded Containers
                </label>
                <input
                  id="docker-exclude-containers"
                  type="text"
                  placeholder="container1, container2"
                  value={dockerExcludeContainers}
                  onChange={(e) => setDockerExcludeContainers(e.target.value)}
                  className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
                />
                <p className="text-[11px] text-slate-500 mt-1">
                  Comma-separated container names or IDs to omit.
                </p>
              </div>

              <div>
                <label htmlFor="docker-source-alias" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                  Source Attribution Alias
                </label>
                <input
                  id="docker-source-alias"
                  type="text"
                  value={dockerSourceAlias}
                  onChange={(e) => setDockerSourceAlias(e.target.value)}
                  maxLength={64}
                  className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
                />
                <p className="text-[11px] text-slate-500 mt-1">
                  Default source alias assigned to container log entries (1 - 64 chars).
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Section 4: Network & Reverse Proxy */}
        <div className="space-y-3 pt-3 border-t border-dark-800">
          <h4 className="text-xs font-semibold text-slate-300 flex items-center gap-2">
            <Network className="w-3.5 h-3.5 text-accent-400" />
            <span>Network & Reverse Proxy</span>
          </h4>
          <div className="space-y-3">
            <div>
              <label htmlFor="trusted-proxies" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Trusted Reverse Proxies
              </label>
              <input
                id="trusted-proxies"
                type="text"
                placeholder="10.0.0.0/8, 192.168.1.1"
                value={trustedProxies}
                onChange={(e) => setTrustedProxies(e.target.value)}
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
              <p className="text-[11px] text-slate-500 mt-1">
                Comma-separated IP addresses or CIDR ranges. Enables X-Forwarded-For parsing.
              </p>
            </div>

            <div className="space-y-2 pt-1">
              <div>
                <label className="flex items-center gap-2.5 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    aria-label="Trust Docker Bridge Networks"
                    checked={trustDockerProxies}
                    onChange={(e) => setTrustDockerProxies(e.target.checked)}
                    className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
                  />
                  <span className="font-medium">Trust Docker Bridge Networks</span>
                </label>
                <p className="text-[11px] text-slate-500 mt-1 pl-6.5">
                  Trusts private Docker subnet 172.16.0.0/12 as reverse proxy gateways.
                </p>
              </div>

              <div>
                <label className="flex items-center gap-2.5 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    aria-label="Force Secure Session Cookies"
                    checked={cookieSecure}
                    onChange={(e) => setCookieSecure(e.target.checked)}
                    className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
                  />
                  <span className="font-medium">Force Secure Session Cookies</span>
                </label>
                <p className="text-[11px] text-slate-500 mt-1 pl-6.5">
                  Applies immediately to inbound client IP extraction and session cookie generation.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Section 5: Syslog TCP Connection Limits */}
        <div className="space-y-3 pt-3 border-t border-dark-800">
          <h4 className="text-xs font-semibold text-slate-300 flex items-center gap-2">
            <Server className="w-3.5 h-3.5 text-accent-400" />
            <span>Syslog TCP Connection Limits</span>
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label htmlFor="syslog-max-tcp-connections" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Max Concurrent TCP Connections
              </label>
              <input
                id="syslog-max-tcp-connections"
                type="number"
                min={1}
                value={syslogMaxTcpConnections}
                onChange={(e) => setSyslogMaxTcpConnections(parseInt(e.target.value, 10) || 1)}
                required
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>

            <div>
              <label htmlFor="syslog-tcp-inactivity-timeout" className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
                Client Inactivity Timeout (seconds)
              </label>
              <input
                id="syslog-tcp-inactivity-timeout"
                type="number"
                min={0}
                step={5}
                value={syslogTcpInactivityTimeout}
                onChange={(e) => setSyslogTcpInactivityTimeout(parseFloat(e.target.value) || 0.0)}
                required
                className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
            </div>
          </div>
          <p className="text-[11px] text-slate-500">
            Takes effect immediately for new connections and active listeners. Setting timeout to 0 keeps connections open indefinitely.
          </p>
        </div>

        {/* Sticky Action Bar: floats while scrolling form, matching Applications tab */}
        {(isDirty || saveInlineSuccess || saveInlineError) && (
          <aside
            aria-label="Unsaved changes bar"
            className={`sticky bottom-4 z-30 bg-dark-900/95 backdrop-blur-md border rounded-xl p-3 sm:px-5 sm:py-3 shadow-2xl flex flex-col sm:flex-row items-center justify-between gap-3 animate-in fade-in slide-in-from-bottom-2 ${
              saveInlineSuccess
                ? 'border-emerald-500/40'
                : saveInlineError
                  ? 'border-red-500/40'
                  : 'border-amber-500/40'
            }`}
          >
            <div className="flex items-center gap-2 text-xs font-medium">
              {saveInlineSuccess ? (
                <div className="flex items-center gap-2 text-emerald-400 font-mono">
                  <Check className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>Settings saved successfully!</span>
                </div>
              ) : saveInlineError ? (
                <div className="flex items-center gap-2 text-red-400 font-mono">
                  <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>{saveInlineError}</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 text-amber-300">
                  <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
                  <span>You have unsaved changes</span>
                </div>
              )}
            </div>

            {isDirty && (
              <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
                {isSaving && (
                  <div className="hidden sm:flex items-center gap-1.5 text-xs text-slate-400 font-mono mr-1">
                    <RefreshCw className="w-3.5 h-3.5 animate-spin text-accent-500" />
                    <span>Saving...</span>
                  </div>
                )}
                <button
                  type="button"
                  onClick={handleReset}
                  disabled={isSaving}
                  className="px-3 py-1.5 text-xs text-slate-300 hover:text-white bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded-lg transition cursor-pointer disabled:opacity-50"
                >
                  Discard
                </button>
                <button
                  type="submit"
                  disabled={isSaving}
                  className="px-4 py-1.5 text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 rounded-lg transition cursor-pointer flex items-center justify-center gap-1.5 shadow-md disabled:opacity-50"
                >
                  {isSaving ? (
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Save className="w-3.5 h-3.5" />
                  )}
                  <span>Save Changes</span>
                </button>
              </div>
            )}
          </aside>
        )}
      </form>
    </section>
  );
};
