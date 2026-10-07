import React, { useState, useEffect, useMemo } from 'react';
import { Sparkles, RefreshCw, ExternalLink } from 'lucide-react';
import { AlertRule, AlertRuleCreate, AlertRuleUpdate, NotificationChannel } from '../../types.ts';
import { createAlertRule, updateAlertRule } from '../../api/alerts.ts';
import { MultiSelectDropdown } from '../common/MultiSelectDropdown.tsx';
import { Modal } from '../common/Modal.tsx';
import { UnsavedChangesModal } from '../common/UnsavedChangesModal.tsx';

export interface AlertRuleModalProps {
  isOpen: boolean;
  ruleToEdit: AlertRule | null;
  channels: NotificationChannel[];
  availableApps: string[];
  isAiConfigured?: boolean;
  onNavigateToSettings?: () => void;
  onClose: () => void;
  onSuccess: (savedRule: AlertRule) => void;
}

export const AlertRuleModal: React.FC<AlertRuleModalProps> = ({
  isOpen,
  ruleToEdit,
  channels,
  availableApps,
  isAiConfigured = true,
  onNavigateToSettings,
  onClose,
  onSuccess,
}) => {
  const [formName, setFormName] = useState<string>('');
  const [formRuleType, setFormRuleType] = useState<string>('threshold');
  const [formChannelId, setFormChannelId] = useState<number | null>(null);
  const [formFilterApps, setFormFilterApps] = useState<string[]>([]);
  const [formFilterSeverity, setFormFilterSeverity] = useState<number | ''>('');
  const [formMatchPattern, setFormMatchPattern] = useState<string>('');
  const [formThresholdCount, setFormThresholdCount] = useState<number | ''>(1);
  const [formWindowSeconds, setFormWindowSeconds] = useState<number | ''>(60);
  const [formCooldownSeconds, setFormCooldownSeconds] = useState<number | ''>(300);
  const [formAiEnrichment, setFormAiEnrichment] = useState<boolean>(false);
  const [formIsEnabled, setFormIsEnabled] = useState<boolean>(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState<boolean>(false);

  useEffect(() => {
    if (!isOpen) {
      setShowDiscardConfirm(false);
      return;
    }
    setShowDiscardConfirm(false);

    if (ruleToEdit) {
      setFormName(ruleToEdit.name);
      setFormRuleType(ruleToEdit.rule_type);
      setFormChannelId(ruleToEdit.channel_id ?? null);
      setFormFilterApps(
        ruleToEdit.filter_app
          ? ruleToEdit.filter_app.split(',').map((s) => s.trim()).filter(Boolean)
          : []
      );
      setFormFilterSeverity(
        ruleToEdit.filter_severity !== null && ruleToEdit.filter_severity !== undefined
          ? ruleToEdit.filter_severity
          : ''
      );
      setFormMatchPattern(ruleToEdit.match_pattern || '');
      setFormThresholdCount(ruleToEdit.threshold_count);
      setFormWindowSeconds(ruleToEdit.window_seconds);
      setFormCooldownSeconds(ruleToEdit.cooldown_seconds);
      setFormAiEnrichment(ruleToEdit.ai_enrichment);
      setFormIsEnabled(ruleToEdit.is_enabled);
      setFormError(null);
    } else {
      setFormName('');
      setFormRuleType('threshold');
      setFormChannelId(channels.length > 0 ? channels[0].id : null);
      setFormFilterApps([]);
      setFormFilterSeverity('');
      setFormMatchPattern('');
      setFormThresholdCount(1);
      setFormWindowSeconds(60);
      setFormCooldownSeconds(300);
      setFormAiEnrichment(false);
      setFormIsEnabled(true);
      setFormError(null);
    }
  }, [isOpen, ruleToEdit, channels]);

  const isDirty = useMemo(() => {
    if (!isOpen) return false;
    if (ruleToEdit) {
      const initialApps = ruleToEdit.filter_app
        ? ruleToEdit.filter_app.split(',').map((s) => s.trim()).filter(Boolean)
        : [];
      const appsChanged =
        formFilterApps.length !== initialApps.length ||
        formFilterApps.some((app, i) => app !== initialApps[i]);
      const initialSeverity =
        ruleToEdit.filter_severity !== null && ruleToEdit.filter_severity !== undefined
          ? ruleToEdit.filter_severity
          : '';
      return (
        formName !== ruleToEdit.name ||
        formRuleType !== ruleToEdit.rule_type ||
        formChannelId !== (ruleToEdit.channel_id ?? null) ||
        appsChanged ||
        formFilterSeverity !== initialSeverity ||
        formMatchPattern !== (ruleToEdit.match_pattern || '') ||
        formThresholdCount !== ruleToEdit.threshold_count ||
        formWindowSeconds !== ruleToEdit.window_seconds ||
        formCooldownSeconds !== ruleToEdit.cooldown_seconds ||
        formAiEnrichment !== ruleToEdit.ai_enrichment ||
        formIsEnabled !== ruleToEdit.is_enabled
      );
    } else {
      const initialChannel = channels.length > 0 ? channels[0].id : null;
      return (
        formName !== '' ||
        formRuleType !== 'threshold' ||
        formChannelId !== initialChannel ||
        formFilterApps.length > 0 ||
        formFilterSeverity !== '' ||
        formMatchPattern !== '' ||
        formThresholdCount !== 1 ||
        formWindowSeconds !== 60 ||
        formCooldownSeconds !== 300 ||
        formAiEnrichment !== false ||
        formIsEnabled !== true
      );
    }
  }, [
    isOpen,
    ruleToEdit,
    channels,
    formName,
    formRuleType,
    formChannelId,
    formFilterApps,
    formFilterSeverity,
    formMatchPattern,
    formThresholdCount,
    formWindowSeconds,
    formCooldownSeconds,
    formAiEnrichment,
    formIsEnabled,
  ]);

  const handleCloseAttempt = () => {
    if (showDiscardConfirm) return;
    if (isDirty) {
      setShowDiscardConfirm(true);
    } else {
      onClose();
    }
  };

  const handleSaveRule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim()) {
      setFormError('Rule name is required.');
      return;
    }

    setIsSubmitting(true);
    setFormError(null);

    const filterAppPayload = formFilterApps.length > 0 ? formFilterApps.join(', ') : null;
    const finalCooldown = formCooldownSeconds === '' ? 0 : Number(formCooldownSeconds);
    const finalThreshold = formThresholdCount === '' ? 1 : Number(formThresholdCount);
    const finalWindow = formWindowSeconds === '' ? 60 : Number(formWindowSeconds);

    try {
      if (ruleToEdit) {
        const cooldownModified = ruleToEdit.cooldown_seconds !== finalCooldown;
        const updatePayload: AlertRuleUpdate = {
          name: formName.trim(),
          rule_type: formRuleType,
          channel_id: formChannelId,
          filter_app: filterAppPayload,
          filter_severity: formFilterSeverity === '' ? null : Number(formFilterSeverity),
          match_pattern: formMatchPattern.trim() ? formMatchPattern.trim() : null,
          threshold_count: (formRuleType === 'threshold' || formRuleType === 'rate') ? finalThreshold : 1,
          window_seconds: finalWindow,
          cooldown_seconds: finalCooldown,
          ai_enrichment: formAiEnrichment,
          is_enabled: formIsEnabled,
          reset_cooldown: cooldownModified,
        };
        const updated = await updateAlertRule(ruleToEdit.id, updatePayload);
        onSuccess(updated);
      } else {
        const createPayload: AlertRuleCreate = {
          name: formName.trim(),
          rule_type: formRuleType,
          channel_id: formChannelId,
          filter_app: filterAppPayload,
          filter_severity: formFilterSeverity === '' ? null : Number(formFilterSeverity),
          match_pattern: formMatchPattern.trim() ? formMatchPattern.trim() : null,
          threshold_count: (formRuleType === 'threshold' || formRuleType === 'rate') ? finalThreshold : 1,
          window_seconds: finalWindow,
          cooldown_seconds: finalCooldown,
          ai_enrichment: formAiEnrichment,
          is_enabled: formIsEnabled,
        };
        const created = await createAlertRule(createPayload);
        onSuccess(created);
      }
      onClose();
    } catch (err: any) {
      setFormError(err.message || 'Failed to save alert rule.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleGoToSettings = () => {
    onClose();
    if (onNavigateToSettings) {
      onNavigateToSettings();
    } else {
      window.history.pushState(null, '', '/settings');
      window.dispatchEvent(new PopStateEvent('popstate'));
    }
  };

  const isEnrichmentDisabled = !isAiConfigured && !ruleToEdit?.ai_enrichment;

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleCloseAttempt}
      title={ruleToEdit ? 'Edit Alert Rule' : 'Create New Alert Rule'}
      maxWidth="max-w-xl"
    >
      <form onSubmit={handleSaveRule} className="space-y-4 text-xs">
        {formError && (
          <div className="p-2.5 bg-red-950/40 border border-red-800/60 rounded text-red-300">
            {formError}
          </div>
        )}

        {/* Rule Name & Doc Link */}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-slate-300 font-medium">Rule Name</label>
            <a
              href="https://github.com/logshed/logshed/blob/main/docs/RULES_GUIDE.md#alert-rules"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[10px] text-accent-400 hover:underline"
              title="Open Alert Rules Documentation"
            >
              <span>Rule Guide &amp; Examples</span>
              <ExternalLink className="w-2.5 h-2.5" />
            </a>
          </div>
          <input
            type="text"
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            placeholder="e.g. Critical Auth Failure Spike"
            required
            className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500"
          />
        </div>

        {/* Rule Type & Target Channel */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-slate-300 font-medium">Rule Type</label>
            <select
              value={formRuleType}
              onChange={(e) => setFormRuleType(e.target.value)}
              className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500"
            >
              <option value="threshold">Threshold (Sliding Window)</option>
              <option value="pattern">Pattern (Immediate Match)</option>
              <option value="rate">Rate Spike (Logs / Second)</option>
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-slate-300 font-medium">Target Channel</label>
            <select
              value={formChannelId ?? ''}
              onChange={(e) => setFormChannelId(e.target.value ? Number(e.target.value) : null)}
              className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500"
            >
              <option value="">All Enabled Channels</option>
              {channels.map((ch) => (
                <option key={ch.id} value={ch.id}>
                  {ch.name}{ch.is_enabled ? '' : ' (Disabled)'}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Rate Spike options placed directly below Rule Type */}
        {formRuleType === 'rate' && (
          <div className="p-3 bg-dark-850/60 rounded-lg border border-dark-800 space-y-2">
            <div className="text-[11px] font-medium text-white">
              Rate Spike Settings (Ingestion Volume)
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">Rate Threshold (logs / second)</label>
                <input
                  type="number"
                  min="1"
                  max="100000"
                  value={formThresholdCount}
                  onChange={(e) =>
                    setFormThresholdCount(
                      e.target.value === '' ? '' : Math.max(1, Number(e.target.value))
                    )
                  }
                  className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500 font-mono"
                />
                <p className="text-[11px] text-slate-500">Average logs per second to trigger alert.</p>
              </div>

              <div className="space-y-1">
                <label className="text-slate-300 font-medium">Measurement Window (seconds)</label>
                <input
                  type="number"
                  min="1"
                  max="86400"
                  value={formWindowSeconds}
                  onChange={(e) =>
                    setFormWindowSeconds(
                      e.target.value === '' ? '' : Math.max(1, Number(e.target.value))
                    )
                  }
                  className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500 font-mono"
                />
                <p className="text-[11px] text-slate-500">Sustained duration window (e.g. 30s).</p>
              </div>
            </div>
            <p className="text-[11px] text-slate-400 pt-1">
              Triggers when incoming volume exceeds the specified rate over the window. Identifies top culprit services and message patterns.
            </p>
          </div>
        )}

        {/* Threshold options placed directly below Rule Type */}
        {formRuleType === 'threshold' && (
          <div className="p-3 bg-dark-850/60 rounded-lg border border-dark-800 space-y-2">
            <div className="text-[11px] font-medium text-white">
              Threshold Settings (Sliding Window)
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-slate-300 font-medium">Threshold Count</label>
                <input
                  type="number"
                  min="1"
                  max="10000"
                  value={formThresholdCount}
                  onChange={(e) =>
                    setFormThresholdCount(
                      e.target.value === '' ? '' : Math.max(1, Number(e.target.value))
                    )
                  }
                  className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500 font-mono"
                />
                <p className="text-[11px] text-slate-500">Number of events to trigger alert.</p>
              </div>

              <div className="space-y-1">
                <label className="text-slate-300 font-medium">Window Duration (seconds)</label>
                <input
                  type="number"
                  min="1"
                  max="86400"
                  value={formWindowSeconds}
                  onChange={(e) =>
                    setFormWindowSeconds(
                      e.target.value === '' ? '' : Math.max(1, Number(e.target.value))
                    )
                  }
                  className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500 font-mono"
                />
                <p className="text-[11px] text-slate-500">Sliding time window duration.</p>
              </div>
            </div>
          </div>
        )}

        {/* App Filter (Multi-Select) & Max Severity Filter */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="space-y-1">
            <label className="text-slate-300 font-medium">App Filter (Optional)</label>
            <MultiSelectDropdown
              label="Application"
              placeholder="All applications"
              options={availableApps}
              selected={formFilterApps}
              onChange={setFormFilterApps}
              allowCustomInput={true}
              variant="form"
            />
          </div>

          <div className="space-y-1">
            <label className="text-slate-300 font-medium">Max Severity Filter (Optional)</label>
            <select
              value={formFilterSeverity}
              onChange={(e) => setFormFilterSeverity(e.target.value === '' ? '' : Number(e.target.value))}
              className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500"
            >
              <option value="">Any Severity</option>
              <option value="0">0 - Emergency</option>
              <option value="1">1 - Alert</option>
              <option value="2">2 - Critical</option>
              <option value="3">3 - Error</option>
              <option value="4">4 - Warning</option>
              <option value="5">5 - Notice</option>
              <option value="6">6 - Info</option>
              <option value="7">7 - Debug</option>
            </select>
          </div>
        </div>

        {/* Match Pattern */}
        <div className="space-y-1">
          <label className="text-slate-300 font-medium">Match Pattern (Regex or Substring)</label>
          <input
            type="text"
            value={formMatchPattern}
            onChange={(e) => setFormMatchPattern(e.target.value)}
            placeholder="e.g. Failed password|authentication failure"
            className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 focus:outline-hidden focus:border-accent-500 font-mono text-xs"
          />
          <p className="text-[11px] text-slate-500">
            Matches against log message text using regular expression search or case-insensitive keyword search.
          </p>
        </div>

        {/* Cooldown */}
        <div className="space-y-1">
          <label className="text-slate-300 font-medium">Cooldown Flap Dampening (seconds)</label>
          <input
            type="number"
            min="0"
            max="86400"
            value={formCooldownSeconds}
            onChange={(e) =>
              setFormCooldownSeconds(
                e.target.value === '' ? '' : Math.max(0, Number(e.target.value))
              )
            }
            className="w-full h-[38px] bg-dark-800 border border-dark-700 rounded-lg px-3 text-slate-200 text-xs focus:outline-hidden focus:border-accent-500 font-mono"
          />
          <p className="text-[11px] text-slate-500">
            Minimum duration to suppress repeat notifications after an alert fires.
          </p>
        </div>

        {/* AI Enrichment & Enable */}
        <div className="pt-2 space-y-2">
          <label className={`flex items-center gap-2.5 ${isEnrichmentDisabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}`}>
            <input
              type="checkbox"
              checked={formAiEnrichment}
              disabled={isEnrichmentDisabled}
              onChange={(e) => setFormAiEnrichment(e.target.checked)}
              className="rounded border-dark-700 bg-dark-800 text-accent-500 focus:ring-0 disabled:cursor-not-allowed"
            />
            <span className="text-slate-200 font-medium flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 text-purple-400" />
              <span>AI Root-Cause Incident Enrichment</span>
            </span>
          </label>
          <p className="text-[11px] text-slate-400 pl-6">
            When an alert fires, redact triggering logs and query the configured LLM to append root cause and remediation insights.
          </p>

          {isEnrichmentDisabled && (
            <div className="ml-6 p-2.5 bg-amber-950/40 border border-amber-800/60 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-amber-300 font-mono">
              <span>AI enrichment requires an active AI provider. Configure an AI provider in Settings to enable enrichment.</span>
              <button
                type="button"
                onClick={handleGoToSettings}
                className="text-accent-400 hover:text-accent-300 underline font-medium shrink-0 cursor-pointer inline-flex items-center gap-1 text-[11px] self-start sm:self-auto"
              >
                <span>Configure in Settings</span>
                <ExternalLink className="w-3 h-3" />
              </button>
            </div>
          )}

          {!isAiConfigured && ruleToEdit?.ai_enrichment && (
            <div className="ml-6 p-2.5 bg-amber-950/40 border border-amber-800/60 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs text-amber-300 font-mono">
              <span>AI provider is disabled or not configured. This rule will not be enriched until an AI provider is enabled in Settings.</span>
              <button
                type="button"
                onClick={handleGoToSettings}
                className="text-accent-400 hover:text-accent-300 underline font-medium shrink-0 cursor-pointer inline-flex items-center gap-1 text-[11px] self-start sm:self-auto"
              >
                <span>Configure in Settings</span>
                <ExternalLink className="w-3 h-3" />
              </button>
            </div>
          )}

          <label className="flex items-center gap-2.5 cursor-pointer pt-1">
            <input
              type="checkbox"
              checked={formIsEnabled}
              onChange={(e) => setFormIsEnabled(e.target.checked)}
              className="rounded border-dark-700 bg-dark-800 text-accent-500 focus:ring-0"
            />
            <span className="text-slate-200 font-medium">Enable Alert Rule</span>
          </label>
        </div>

        <div className="pt-4 border-t border-dark-700 flex justify-end gap-2">
          <button
            type="button"
            onClick={handleCloseAttempt}
            className="px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 hover:bg-dark-750 transition cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSubmitting}
            className="px-4 py-1.5 rounded-lg text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 transition cursor-pointer flex items-center gap-1.5 disabled:opacity-50"
          >
            {isSubmitting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : null}
            <span>{ruleToEdit ? 'Update Rule' : 'Create Rule'}</span>
          </button>
        </div>
      </form>

      <UnsavedChangesModal
        isOpen={showDiscardConfirm}
        onKeepEditing={() => setShowDiscardConfirm(false)}
        onDiscard={() => {
          setShowDiscardConfirm(false);
          onClose();
        }}
      />
    </Modal>
  );
};
