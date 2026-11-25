import React, { useEffect, useMemo, useState } from 'react';
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
import { useSavedForm } from './useSavedForm.ts';

const DEFAULT_THINKING_LEVEL: AiThinkingLevel = 'low';

const THINKING_LEVEL_OPTIONS: { value: AiThinkingLevel; label: string }[] = [
  { value: 'off', label: 'Off (or the lowest level)' },
  { value: 'low', label: 'Low (default)' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

interface AdvancedValues {
  ai_timeout: number;
  ai_thinking_level: AiThinkingLevel;
  app_url: string;
  allow_private_notification_targets: boolean;
  trusted_proxies: string;
  trust_docker_proxies: boolean;
  cookie_secure: boolean;
  syslog_max_tcp_connections: number;
  syslog_tcp_inactivity_timeout: number;
}

const ADVANCED_DEFAULTS: AdvancedValues = {
  ai_timeout: 45.0,
  ai_thinking_level: DEFAULT_THINKING_LEVEL,
  app_url: '',
  allow_private_notification_targets: true,
  trusted_proxies: '',
  trust_docker_proxies: false,
  cookie_secure: false,
  syslog_max_tcp_connections: 250,
  syslog_tcp_inactivity_timeout: 0.0,
};

const advancedValues = (s: SystemSettings): AdvancedValues => ({
  ai_timeout: s.ai_timeout ?? ADVANCED_DEFAULTS.ai_timeout,
  ai_thinking_level: s.ai_thinking_level ?? ADVANCED_DEFAULTS.ai_thinking_level,
  app_url: s.app_url ?? '',
  allow_private_notification_targets: s.allow_private_notification_targets ?? true,
  trusted_proxies: s.trusted_proxies ?? '',
  trust_docker_proxies: s.trust_docker_proxies ?? false,
  cookie_secure: s.cookie_secure ?? false,
  syslog_max_tcp_connections: s.syslog_max_tcp_connections ?? 250,
  syslog_tcp_inactivity_timeout: s.syslog_tcp_inactivity_timeout ?? 0.0,
});

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
  const savedValues = useMemo(() => (settings ? advancedValues(settings) : null), [settings]);
  const form = useSavedForm<AdvancedValues>(savedValues, ADVANCED_DEFAULTS, {
    app_url: (v) => v.trim(),
    trusted_proxies: (v) => v.trim(),
  });
  const {
    ai_timeout: aiTimeout,
    ai_thinking_level: aiThinkingLevel,
    app_url: appUrl,
    allow_private_notification_targets: allowPrivate,
    trusted_proxies: trustedProxies,
    trust_docker_proxies: trustDockerProxies,
    cookie_secure: cookieSecure,
    syslog_max_tcp_connections: syslogMaxTcpConnections,
    syslog_tcp_inactivity_timeout: syslogTcpInactivityTimeout,
  } = form.values;
  const isDirty = form.isDirty;

  // Status state
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveInlineError, setSaveInlineError] = useState<string | null>(null);
  const [savedFlash, flashSaved, setSavedFlash] = useFlash<true>(3000);
  const saveInlineSuccess = savedFlash !== null;

  useEffect(() => {
    onDirtyChange?.(isDirty);
  }, [isDirty, onDirtyChange]);

  const handleReset = () => {
    form.reset();
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

    const updates: Partial<SystemSettings> = form.changes();
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
              onChange={(e) => form.set('ai_timeout', parseFloat(e.target.value) || 5)}
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
                  onChange={(v) => form.set('ai_thinking_level', v as AiThinkingLevel)}
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
              onChange={(e) => form.set('app_url', e.target.value)}
              mono
            />

            <div className="pt-1">
              <Checkbox
                label="Allow Local / LAN Webhook Targets"
                checked={allowPrivate}
                onChange={(e) => form.set('allow_private_notification_targets', e.target.checked)}
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
              onChange={(e) => form.set('trusted_proxies', e.target.value)}
              mono
            />

            <div className="space-y-2 pt-1">
              <div>
                <Checkbox
                  label="Trust Docker Bridge Networks"
                  checked={trustDockerProxies}
                  onChange={(e) => form.set('trust_docker_proxies', e.target.checked)}
                />
                <p className="text-[11px] text-fg-muted mt-1 pl-6">
                  Trusts private Docker subnet 172.16.0.0/12 as reverse proxy gateways.
                </p>
              </div>

              <div>
                <Checkbox
                  label="Force Secure Session Cookies"
                  checked={cookieSecure}
                  onChange={(e) => form.set('cookie_secure', e.target.checked)}
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
              onChange={(e) => form.set('syslog_max_tcp_connections', parseInt(e.target.value, 10) || 1)}
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
              onChange={(e) => form.set('syslog_tcp_inactivity_timeout', parseFloat(e.target.value) || 0.0)}
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
