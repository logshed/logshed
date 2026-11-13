import React, { useState, useEffect } from 'react';
import {
  Sliders,
  Cpu,
  Bell,
  Network,
  Server,
} from 'lucide-react';
import { Card, CardHeader } from '../common/Card.tsx';
import { Checkbox, Field, SELECT_FIELD_CLASS, TextField } from '../common/Field.tsx';
import { StickySaveBar } from './StickySaveBar.tsx';
import { AiThinkingLevel, SystemSettings } from '../../types.ts';
import { SelectDropdown } from '../common/SelectDropdown.tsx';
import { updateSettings } from '../../api/settings.ts';
import { useFlash } from '../../utils/hooks.ts';

const DEFAULT_THINKING_LEVEL: AiThinkingLevel = 'low';

const THINKING_LEVEL_OPTIONS: { value: AiThinkingLevel; label: string }[] = [
  { value: 'off', label: 'Off (or the lowest level)' },
  { value: 'low', label: 'Low (default)' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

export interface AdvancedSettingsCardProps {
  settings: SystemSettings | null;
  onSettingsSaved: () => Promise<void> | void;
  onDirtyChange?: (dirty: boolean) => void;
  saveTriggerRef?: React.MutableRefObject<(() => Promise<boolean>) | null>;
}

export const AdvancedSettingsCard: React.FC<AdvancedSettingsCardProps> = ({
  settings,
  onSettingsSaved,
  onDirtyChange,
  saveTriggerRef,
}) => {
  // Form state
  const [aiTimeout, setAiTimeout] = useState<number>(45.0);
  const [aiThinkingLevel, setAiThinkingLevel] = useState<AiThinkingLevel>(DEFAULT_THINKING_LEVEL);
  const [appUrl, setAppUrl] = useState<string>('');
  const [allowPrivate, setAllowPrivate] = useState<boolean>(true);
  const [trustedProxies, setTrustedProxies] = useState<string>('');
  const [trustDockerProxies, setTrustDockerProxies] = useState<boolean>(false);
  const [cookieSecure, setCookieSecure] = useState<boolean>(false);
  const [syslogMaxTcpConnections, setSyslogMaxTcpConnections] = useState<number>(250);
  const [syslogTcpInactivityTimeout, setSyslogTcpInactivityTimeout] = useState<number>(0.0);

  // Status state
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveInlineError, setSaveInlineError] = useState<string | null>(null);
  const [savedFlash, flashSaved, setSavedFlash] = useFlash<true>(3000);
  const saveInlineSuccess = savedFlash !== null;

  // Populate from settings
  useEffect(() => {
    if (settings) {
      setAiTimeout(settings.ai_timeout ?? 45.0);
      setAiThinkingLevel(settings.ai_thinking_level ?? DEFAULT_THINKING_LEVEL);
      setAppUrl(settings.app_url ?? '');
      setAllowPrivate(settings.allow_private_notification_targets ?? true);
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
      aiThinkingLevel !== (settings.ai_thinking_level ?? DEFAULT_THINKING_LEVEL) ||
      appUrl.trim() !== (settings.app_url ?? '') ||
      allowPrivate !== (settings.allow_private_notification_targets ?? true) ||
      trustedProxies.trim() !== (settings.trusted_proxies ?? '') ||
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
    setAiThinkingLevel(settings.ai_thinking_level ?? DEFAULT_THINKING_LEVEL);
    setAppUrl(settings.app_url ?? '');
    setAllowPrivate(settings.allow_private_notification_targets ?? true);
    setTrustedProxies(settings.trusted_proxies ?? '');
    setTrustDockerProxies(settings.trust_docker_proxies ?? false);
    setCookieSecure(settings.cookie_secure ?? false);
    setSyslogMaxTcpConnections(settings.syslog_max_tcp_connections ?? 250);
    setSyslogTcpInactivityTimeout(settings.syslog_tcp_inactivity_timeout ?? 0.0);
    setSaveInlineError(null);
    setSavedFlash(null);
  };

  // Resolves true when the edits were saved (or there was nothing to save), false on a validation
  // or save failure, so a navigation guard knows whether it may continue.
  const performSave = async (): Promise<boolean> => {
    if (!settings) return true;
    setSaveInlineError(null);
    setSavedFlash(null);

    // Validation
    if (aiTimeout <= 0) {
      setSaveInlineError('Request Timeout must be greater than 0 seconds.');
      return false;
    }
    if (syslogMaxTcpConnections < 1) {
      setSaveInlineError('Max Concurrent TCP Connections must be at least 1.');
      return false;
    }
    if (syslogTcpInactivityTimeout < 0) {
      setSaveInlineError('Client Inactivity Timeout cannot be negative.');
      return false;
    }

    const updates: Partial<SystemSettings> = {};
    if (aiTimeout !== (settings.ai_timeout ?? 45.0)) {
      updates.ai_timeout = aiTimeout;
    }
    if (aiThinkingLevel !== (settings.ai_thinking_level ?? DEFAULT_THINKING_LEVEL)) {
      updates.ai_thinking_level = aiThinkingLevel;
    }
    const trimmedAppUrl = appUrl.trim();
    if (trimmedAppUrl !== (settings.app_url ?? '')) {
      updates.app_url = trimmedAppUrl;
    }
    if (allowPrivate !== (settings.allow_private_notification_targets ?? true)) {
      updates.allow_private_notification_targets = allowPrivate;
    }
    const trimmedProxies = trustedProxies.trim();
    if (trimmedProxies !== (settings.trusted_proxies ?? '')) {
      updates.trusted_proxies = trimmedProxies;
    }
    if (trustDockerProxies !== (settings.trust_docker_proxies ?? false)) {
      updates.trust_docker_proxies = trustDockerProxies;
    }
    if (cookieSecure !== (settings.cookie_secure ?? false)) {
      updates.cookie_secure = cookieSecure;
    }
    if (syslogMaxTcpConnections !== (settings.syslog_max_tcp_connections ?? 250)) {
      updates.syslog_max_tcp_connections = syslogMaxTcpConnections;
    }
    if (syslogTcpInactivityTimeout !== (settings.syslog_tcp_inactivity_timeout ?? 0.0)) {
      updates.syslog_tcp_inactivity_timeout = syslogTcpInactivityTimeout;
    }

    if (Object.keys(updates).length === 0) {
      return true;
    }

    try {
      setIsSaving(true);
      await updateSettings(updates);

      flashSaved(true);

      await onSettingsSaved();
      return true;
    } catch (err: any) {
      setSaveInlineError(err.message || 'Failed to save advanced system settings.');
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    await performSave();
  };

  // Expose the save so a navigation guard (Save & Continue) can trigger it.
  useEffect(() => {
    if (saveTriggerRef) {
      saveTriggerRef.current = performSave;
    }
    return () => {
      if (saveTriggerRef) {
        saveTriggerRef.current = null;
      }
    };
  });

  return (
    <Card>
      <CardHeader
        icon={<Sliders />}
        title="Advanced System Settings"
        description="Configure internal collector limits, network proxy subnets, and AI execution boundaries."
      />

      <form onSubmit={handleSave} className="space-y-6">
        {/* Section 1: AI Engine Limits */}
        <div className="space-y-3 pt-1 border-t border-line-subtle">
          <h4 className="text-xs font-semibold text-fg-soft flex items-center gap-2">
            <Cpu className="w-3.5 h-3.5 text-accent-400" />
            <span>AI Engine Limits</span>
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <TextField
              label="Request Timeout (seconds)"
              id="ai-timeout"
              type="number"
              min={5}
              step={5}
              value={aiTimeout}
              onChange={(e) => setAiTimeout(parseFloat(e.target.value) || 5)}
              required
              mono
            />

            <Field label="Thinking Level" id="ai-thinking-level">
              {({ id, labelId }) => (
                <SelectDropdown
                  variant="panel"
                  fieldClassName={SELECT_FIELD_CLASS}
                  id={id}
                  labelledBy={labelId}
                  label="Thinking Level"
                  options={THINKING_LEVEL_OPTIONS}
                  value={aiThinkingLevel}
                  onChange={(v) => setAiThinkingLevel(v as AiThinkingLevel)}
                />
              )}
            </Field>
          </div>
          <p className="text-[11px] text-fg-muted">
            The timeout applies to every AI provider. The thinking level sets how much reasoning models (Gemini 2.5 and later, Claude 3.7 and later, OpenAI o-series and GPT-5 and later) think before they answer: more thinking can find deeper causes but takes longer and uses more tokens. Claude 5 and later, Gemini 2.5 Pro and Gemini 3 and later still think a little at Off: LogShed asks them for their lowest level.
          </p>
        </div>

        {/* Section 2: Notifications & Webhooks */}
        <div className="space-y-3 pt-3 border-t border-line-subtle">
          <h4 className="text-xs font-semibold text-fg-soft flex items-center gap-2">
            <Bell className="w-3.5 h-3.5 text-accent-400" />
            <span>Notifications & Webhooks</span>
          </h4>
          <div className="space-y-3">
            <TextField
              label="Public Instance URL"
              hint="Used in alert notifications for a link that opens the incident."
              id="app-url"
              type="text"
              placeholder="https://logs.example.com"
              value={appUrl}
              onChange={(e) => setAppUrl(e.target.value)}
              mono
            />

            <div className="pt-1">
              <Checkbox
                label="Allow Local / LAN Webhook Targets"
                checked={allowPrivate}
                onChange={(e) => setAllowPrivate(e.target.checked)}
              />
              <p className="text-[11px] text-fg-muted mt-1 pl-6">
                Permits webhooks targeting private IPv4/IPv6 ranges (e.g. local Gotify or Home Assistant).
              </p>
            </div>
          </div>
        </div>

        {/* Section 3: Network & Reverse Proxy */}
        <div className="space-y-3 pt-3 border-t border-line-subtle">
          <h4 className="text-xs font-semibold text-fg-soft flex items-center gap-2">
            <Network className="w-3.5 h-3.5 text-accent-400" />
            <span>Network & Reverse Proxy</span>
          </h4>
          <div className="space-y-3">
            <TextField
              label="Trusted Reverse Proxies"
              hint="Comma-separated IP addresses or CIDR ranges. Enables X-Forwarded-For parsing. Add 127.0.0.1 for a proxy or tunnel that shares LogShed's network."
              id="trusted-proxies"
              type="text"
              placeholder="10.0.0.0/8, 192.168.1.1"
              value={trustedProxies}
              onChange={(e) => setTrustedProxies(e.target.value)}
              mono
            />

            <div className="space-y-2 pt-1">
              <div>
                <Checkbox
                  label="Trust Docker Bridge Networks"
                  checked={trustDockerProxies}
                  onChange={(e) => setTrustDockerProxies(e.target.checked)}
                />
                <p className="text-[11px] text-fg-muted mt-1 pl-6">
                  Trusts private Docker subnet 172.16.0.0/12 as reverse proxy gateways.
                </p>
              </div>

              <div>
                <Checkbox
                  label="Force Secure Session Cookies"
                  checked={cookieSecure}
                  onChange={(e) => setCookieSecure(e.target.checked)}
                />
                <p className="text-[11px] text-fg-muted mt-1 pl-6">
                  Applies immediately to inbound client IP extraction and session cookie generation.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Section 4: Syslog TCP Connection Limits */}
        <div className="space-y-3 pt-3 border-t border-line-subtle">
          <h4 className="text-xs font-semibold text-fg-soft flex items-center gap-2">
            <Server className="w-3.5 h-3.5 text-accent-400" />
            <span>Syslog TCP Connection Limits</span>
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <TextField
              label="Max Concurrent TCP Connections"
              id="syslog-max-tcp-connections"
              type="number"
              min={1}
              value={syslogMaxTcpConnections}
              onChange={(e) => setSyslogMaxTcpConnections(parseInt(e.target.value, 10) || 1)}
              required
              mono
            />

            <TextField
              label="Client Inactivity Timeout (seconds)"
              id="syslog-tcp-inactivity-timeout"
              type="number"
              min={0}
              step={5}
              value={syslogTcpInactivityTimeout}
              onChange={(e) => setSyslogTcpInactivityTimeout(parseFloat(e.target.value) || 0.0)}
              required
              mono
            />
          </div>
          <p className="text-[11px] text-fg-muted">
            Takes effect immediately for new connections and active listeners. Setting timeout to 0 keeps connections open indefinitely.
          </p>
        </div>

        <StickySaveBar
          isDirty={isDirty}
          isSavingSettings={isSaving}
          saveInlineSuccess={saveInlineSuccess}
          saveNotice={null}
          saveInlineError={saveInlineError}
          onDiscard={handleReset}
        />
      </form>
    </Card>
  );
};
