import React, { useState, useEffect } from 'react';
import { Play, RotateCcw } from 'lucide-react';
import { DropRule } from '../../types.ts';
import { createDropRule, updateDropRule, resetDropRuleCounter, testDropRule } from '../../api/dropRules.ts';
import { Modal } from '../common/Modal.tsx';

interface CreateDropRuleModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: (rule: DropRule) => void;
  initialSource?: string;
  initialApp?: string;
  initialMessage?: string;
  initialName?: string;
  availableSources?: string[];
  availableApps?: string[];
  ruleToEdit?: DropRule | null;
}

export const CreateDropRuleModal: React.FC<CreateDropRuleModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
  initialSource = '',
  initialApp = '',
  initialMessage = '',
  initialName = '',
  availableSources = [],
  availableApps = [],
  ruleToEdit = null,
}) => {
  const [ruleName, setRuleName] = useState<string>('');
  const [messagePattern, setMessagePattern] = useState<string>('');
  const [isRegex, setIsRegex] = useState<boolean>(false);
  const [sourcePattern, setSourcePattern] = useState<string>('');
  const [isCustomSource, setIsCustomSource] = useState<boolean>(false);
  const [appPattern, setAppPattern] = useState<string>('');
  const [isCustomApp, setIsCustomApp] = useState<boolean>(false);
  const [severityThreshold, setSeverityThreshold] = useState<number | null>(null);
  const [isEnabled, setIsEnabled] = useState<boolean>(true);
  const [currentCount, setCurrentCount] = useState<number>(0);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isResetting, setIsResetting] = useState<boolean>(false);

  // Interactive tester states
  const [sampleMessage, setSampleMessage] = useState<string>('');
  const [sampleSource, setSampleSource] = useState<string>('');
  const [sampleApp, setSampleApp] = useState<string>('');
  const [sampleSeverity, setSampleSeverity] = useState<string>('');
  const [testResult, setTestResult] = useState<{ matched?: boolean; error?: string | null } | null>(null);
  const [isTesting, setIsTesting] = useState<boolean>(false);

  // Initialize form state on open or when target rule / initial values change
  useEffect(() => {
    if (isOpen) {
      setTestResult(null);
      if (ruleToEdit) {
        setRuleName(ruleToEdit.name || ruleToEdit.app_pattern || ruleToEdit.source_pattern || '');
        setMessagePattern(ruleToEdit.message_pattern || '');
        setIsRegex(ruleToEdit.is_regex || false);
        setSeverityThreshold(ruleToEdit.severity_threshold ?? null);
        setIsEnabled(ruleToEdit.is_enabled);
        setCurrentCount(ruleToEdit.dropped_count || 0);

        if (ruleToEdit.source_pattern) {
          setSourcePattern(ruleToEdit.source_pattern);
          setIsCustomSource(!availableSources.includes(ruleToEdit.source_pattern));
        } else {
          setSourcePattern('');
          setIsCustomSource(false);
        }

        if (ruleToEdit.app_pattern) {
          setAppPattern(ruleToEdit.app_pattern);
          setIsCustomApp(!availableApps.includes(ruleToEdit.app_pattern));
        } else {
          setAppPattern('');
          setIsCustomApp(false);
        }

        setSampleMessage(ruleToEdit.message_pattern || '');
        setSampleSource(ruleToEdit.source_pattern || '');
        setSampleApp(ruleToEdit.app_pattern || '');
        setSampleSeverity(ruleToEdit.severity_threshold != null ? String(ruleToEdit.severity_threshold) : '');
      } else {
        setRuleName(initialName || (initialApp ? `Drop ${initialApp} Chatter` : (initialSource ? `Drop ${initialSource}` : '')));
        setMessagePattern(initialMessage);
        setIsRegex(false);
        setSeverityThreshold(null);
        setIsEnabled(true);
        setCurrentCount(0);

        if (initialSource) {
          setSourcePattern(initialSource);
          setIsCustomSource(!availableSources.includes(initialSource));
        } else {
          setSourcePattern('');
          setIsCustomSource(false);
        }

        if (initialApp) {
          setAppPattern(initialApp);
          setIsCustomApp(!availableApps.includes(initialApp));
        } else {
          setAppPattern('');
          setIsCustomApp(false);
        }

        setSampleMessage(initialMessage);
        setSampleSource(initialSource);
        setSampleApp(initialApp);
        setSampleSeverity('');
      }
    }
  }, [isOpen, ruleToEdit, initialName, initialSource, initialApp, initialMessage, availableSources, availableApps]);

  const hasFilterCriteria = Boolean(
    messagePattern.trim() || sourcePattern.trim() || appPattern.trim() || severityThreshold !== null
  );

  const handleTestPattern = async () => {
    if (!hasFilterCriteria || !sampleMessage.trim()) return;
    try {
      setIsTesting(true);
      setTestResult(null);
      const res = await testDropRule({
        message_pattern: messagePattern.trim() || '*',
        is_regex: isRegex,
        source_pattern: sourcePattern.trim() || undefined,
        app_pattern: appPattern.trim() || undefined,
        severity_threshold: severityThreshold,
        sample_message: sampleMessage,
        sample_source: sampleSource.trim() || undefined,
        sample_app: sampleApp.trim() || undefined,
        sample_severity: sampleSeverity !== '' ? Number(sampleSeverity) : undefined,
      });
      setTestResult(res);
    } catch (err: any) {
      setTestResult({ error: err.message || 'Test evaluation failed.' });
    } finally {
      setIsTesting(false);
    }
  };

  const handleResetInModal = async () => {
    if (!ruleToEdit) return;
    try {
      setIsResetting(true);
      const updated = await resetDropRuleCounter(ruleToEdit.id);
      setCurrentCount(0);
      onSuccess?.(updated);
    } catch (err: any) {
      setTestResult({ error: err.message || 'Failed to reset counter.' });
    } finally {
      setIsResetting(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ruleName.trim()) {
      setTestResult({ error: 'Rule name is required.' });
      return;
    }
    if (!hasFilterCriteria) return;

    const effectivePattern = messagePattern.trim() || '*';
    try {
      setIsSubmitting(true);
      if (ruleToEdit) {
        const updated = await updateDropRule(ruleToEdit.id, {
          name: ruleName.trim(),
          message_pattern: effectivePattern,
          is_regex: isRegex,
          source_pattern: sourcePattern.trim() || null,
          app_pattern: appPattern.trim() || null,
          severity_threshold: severityThreshold,
          is_enabled: isEnabled,
        });
        onSuccess?.(updated);
      } else {
        const created = await createDropRule({
          name: ruleName.trim(),
          message_pattern: effectivePattern,
          is_regex: isRegex,
          source_pattern: sourcePattern.trim() || undefined,
          app_pattern: appPattern.trim() || undefined,
          severity_threshold: severityThreshold ?? undefined,
          is_enabled: isEnabled,
        });
        onSuccess?.(created);
      }
      onClose();
    } catch (err: any) {
      setTestResult({
        error: err.message || (ruleToEdit ? 'Failed to update drop rule.' : 'Failed to create drop rule.'),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={ruleToEdit ? 'Edit Ingestion Drop Rule' : 'Create Ingestion Drop Rule'}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4 text-xs font-sans">
        <div>
          <label className="block text-slate-300 font-medium mb-1">
            Rule Name <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={ruleName}
            onChange={(e) => setRuleName(e.target.value)}
            placeholder="e.g. CRON Chatter or Docker Healthchecks"
            maxLength={100}
            required
            autoFocus
            className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-1.5 text-slate-200 focus:outline-hidden focus:border-accent-500"
          />
        </div>

        <div>
          <label className="block text-slate-300 font-medium mb-1">
            Message Pattern <span className="text-slate-400 font-normal text-[11px]">(Optional if Host or App is selected)</span>
          </label>
          <input
            type="text"
            value={messagePattern}
            onChange={(e) => setMessagePattern(e.target.value)}
            placeholder="e.g. DHCPACK, query\s+from\b, probe, or * to match all messages"
            maxLength={500}
            className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-1.5 text-slate-200 font-mono focus:outline-hidden focus:border-accent-500"
          />
          <span className="text-[11px] text-slate-400 mt-1 block">
            Case-insensitive match string. Use * or leave empty to drop all messages matching the selected host or app.
          </span>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="modal-is-regex-checkbox"
            checked={isRegex}
            onChange={(e) => setIsRegex(e.target.checked)}
            className="rounded bg-dark-950 border-dark-700 text-accent-500 focus:ring-accent-500/20"
          />
          <label htmlFor="modal-is-regex-checkbox" className="text-slate-300 cursor-pointer">
            Interpret message pattern as Regular Expression
          </label>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {/* Host / IP Filter */}
          <div>
            <label htmlFor="modal-host-select" className="block text-slate-300 font-medium mb-1">
              Host / IP Filter (Optional)
            </label>
            <select
              id="modal-host-select"
              value={isCustomSource ? '__custom__' : (availableSources.includes(sourcePattern) ? sourcePattern : (sourcePattern ? '__custom__' : ''))}
              onChange={(e) => {
                if (e.target.value === '__custom__') {
                  setIsCustomSource(true);
                  if (availableSources.includes(sourcePattern)) {
                    setSourcePattern('');
                  }
                } else {
                  setIsCustomSource(false);
                  setSourcePattern(e.target.value);
                  if (!sampleSource.trim() && e.target.value) {
                    setSampleSource(e.target.value);
                  }
                }
              }}
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-1.5 text-slate-200 font-mono text-xs focus:outline-hidden focus:border-accent-500"
            >
              <option value="">Any Host / IP (None)</option>
              <option value="__custom__">+ Custom wildcard pattern...</option>
              {availableSources.map((src) => (
                <option key={src} value={src}>{src}</option>
              ))}
            </select>

            {isCustomSource && (
              <div className="mt-1.5 flex items-center gap-1.5 animate-in fade-in duration-150">
                <input
                  type="text"
                  value={sourcePattern}
                  onChange={(e) => {
                    setSourcePattern(e.target.value);
                    if (!sampleSource.trim() && e.target.value) {
                      setSampleSource(e.target.value);
                    }
                  }}
                  placeholder="e.g. 192.168.1.*, router*, *dns*"
                  maxLength={255}
                  autoFocus
                  className="flex-1 bg-dark-950 border border-dark-700 rounded px-2.5 py-1 text-slate-200 font-mono text-xs focus:outline-hidden focus:border-accent-500"
                />
                <button
                  type="button"
                  onClick={() => {
                    setIsCustomSource(false);
                    setSourcePattern('');
                  }}
                  className="px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200 bg-dark-900 border border-dark-700 rounded transition shrink-0 cursor-pointer"
                >
                  Select from list
                </button>
              </div>
            )}
          </div>

          {/* Application or Container Filter */}
          <div>
            <label htmlFor="modal-app-select" className="block text-slate-300 font-medium mb-1">
              Application or Container (Optional)
            </label>
            <select
              id="modal-app-select"
              value={isCustomApp ? '__custom__' : (availableApps.includes(appPattern) ? appPattern : (appPattern ? '__custom__' : ''))}
              onChange={(e) => {
                if (e.target.value === '__custom__') {
                  setIsCustomApp(true);
                  if (availableApps.includes(appPattern)) {
                    setAppPattern('');
                  }
                } else {
                  setIsCustomApp(false);
                  setAppPattern(e.target.value);
                  if (!sampleApp.trim() && e.target.value) {
                    setSampleApp(e.target.value);
                  }
                }
              }}
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-1.5 text-slate-200 font-mono text-xs focus:outline-hidden focus:border-accent-500"
            >
              <option value="">Any App / Container (None)</option>
              <option value="__custom__">+ Custom wildcard pattern...</option>
              {availableApps.map((app) => (
                <option key={app} value={app}>{app}</option>
              ))}
            </select>

            {isCustomApp && (
              <div className="mt-1.5 flex items-center gap-1.5 animate-in fade-in duration-150">
                <input
                  type="text"
                  value={appPattern}
                  onChange={(e) => {
                    setAppPattern(e.target.value);
                    if (!sampleApp.trim() && e.target.value) {
                      setSampleApp(e.target.value);
                    }
                  }}
                  placeholder="e.g. dnsmasq, smartbulb*, traefik"
                  maxLength={255}
                  autoFocus
                  className="flex-1 bg-dark-950 border border-dark-700 rounded px-2.5 py-1 text-slate-200 font-mono text-xs focus:outline-hidden focus:border-accent-500"
                />
                <button
                  type="button"
                  onClick={() => {
                    setIsCustomApp(false);
                    setAppPattern('');
                  }}
                  className="px-2 py-1 text-[11px] text-slate-400 hover:text-slate-200 bg-dark-900 border border-dark-700 rounded transition shrink-0 cursor-pointer"
                >
                  Select from list
                </button>
              </div>
            )}
          </div>
        </div>

        <div>
          <label htmlFor="modal-severity-select" className="block text-slate-300 font-medium mb-1">
            Drop if severity is...
          </label>
          <select
            id="modal-severity-select"
            value={severityThreshold === null ? '' : severityThreshold.toString()}
            onChange={(e) => setSeverityThreshold(e.target.value === '' ? null : Number(e.target.value))}
            className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-1.5 text-slate-200 font-mono text-xs focus:outline-hidden focus:border-accent-500"
          >
            <option value="">Any severity</option>
            <option value="7">Debug (7) and below</option>
            <option value="6">Info (6) and below</option>
            <option value="5">Notice (5) and below</option>
            <option value="4">Warning (4) and below</option>
            <option value="3">Error (3) and below</option>
          </select>
          <span className="text-[11px] text-slate-400 mt-1 block">
            Logs at or below this severity level will be dropped. Leave blank to match all severity levels.
          </span>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="modal-is-enabled-checkbox"
            checked={isEnabled}
            onChange={(e) => setIsEnabled(e.target.checked)}
            className="rounded bg-dark-950 border-dark-700 text-accent-500 focus:ring-accent-500/20"
          />
          <label htmlFor="modal-is-enabled-checkbox" className="text-slate-300 cursor-pointer">
            {ruleToEdit ? 'Rule active' : 'Rule enabled immediately upon creation'}
          </label>
        </div>

        {/* Interactive Pattern Tester Section */}
        <div className="bg-dark-950 border border-dark-800 rounded-lg p-3 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-slate-200 text-xs">Test Pattern Match</span>
            <button
              type="button"
              onClick={handleTestPattern}
              disabled={isTesting || !hasFilterCriteria || !sampleMessage.trim()}
              className="flex items-center gap-1 px-2 py-0.5 rounded bg-dark-800 hover:bg-dark-700 text-slate-300 border border-dark-700 transition disabled:opacity-50 cursor-pointer text-[11px]"
            >
              <Play className="w-3 h-3 text-accent-400" />
              <span>{isTesting ? 'Testing...' : 'Dry-Run Test'}</span>
            </button>
          </div>

          <div>
            <label className="block text-slate-400 text-[11px] mb-1">Sample Log Message</label>
            <textarea
              rows={2}
              value={sampleMessage}
              onChange={(e) => setSampleMessage(e.target.value)}
              placeholder="Paste a representative log line here..."
              className="w-full bg-dark-900 border border-dark-800 rounded px-2.5 py-1.5 text-slate-200 font-mono text-xs focus:outline-hidden focus:border-accent-500"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div>
              <label className="block text-slate-400 text-[11px] mb-0.5">Sample Host / IP</label>
              <input
                type="text"
                value={sampleSource}
                onChange={(e) => setSampleSource(e.target.value)}
                placeholder="e.g. 192.168.1.50"
                className="w-full bg-dark-900 border border-dark-800 rounded px-2 py-1 text-slate-200 font-mono text-xs focus:outline-hidden"
              />
            </div>
            <div>
              <label className="block text-slate-400 text-[11px] mb-0.5">Sample App / Container</label>
              <input
                type="text"
                value={sampleApp}
                onChange={(e) => setSampleApp(e.target.value)}
                placeholder="e.g. dnsmasq"
                className="w-full bg-dark-900 border border-dark-800 rounded px-2 py-1 text-slate-200 font-mono text-xs focus:outline-hidden"
              />
            </div>
            <div>
              <label htmlFor="modal-sample-severity-select" className="block text-slate-400 text-[11px] mb-0.5">Sample Severity</label>
              <select
                id="modal-sample-severity-select"
                value={sampleSeverity}
                onChange={(e) => setSampleSeverity(e.target.value)}
                className="w-full bg-dark-900 border border-dark-800 rounded px-2 py-1 text-slate-200 font-mono text-xs focus:outline-hidden"
              >
                <option value="">Unspecified</option>
                <option value="7">Debug (7)</option>
                <option value="6">Info (6)</option>
                <option value="5">Notice (5)</option>
                <option value="4">Warning (4)</option>
                <option value="3">Error (3)</option>
                <option value="2">Critical (2)</option>
                <option value="1">Alert (1)</option>
                <option value="0">Emergency (0)</option>
              </select>
            </div>
          </div>

          {testResult && (
            <div
              className={`p-2 rounded border text-xs flex items-center justify-between animate-in fade-in duration-100 ${
                testResult.error
                  ? 'bg-red-950/40 border-red-800/60 text-red-300'
                  : testResult.matched
                  ? 'bg-emerald-950/40 border-emerald-800/60 text-emerald-300'
                  : 'bg-amber-950/40 border-amber-800/60 text-amber-300'
              }`}
            >
              <div className="flex items-center gap-1.5">
                <span className="font-semibold">
                  {testResult.error
                    ? 'Error:'
                    : testResult.matched
                    ? 'Rule Matched'
                    : 'No Match'}
                </span>
                <span className="text-[11px]">
                  {testResult.error
                    ? testResult.error
                    : testResult.matched
                    ? 'Incoming logs matching this sample would be discarded.'
                    : 'The sample did not match the pattern or criteria.'}
                </span>
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 pt-2 border-t border-dark-800">
          {ruleToEdit ? (
            <button
              type="button"
              onClick={handleResetInModal}
              disabled={isResetting || currentCount === 0}
              aria-label="Reset counter to 0"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-xs text-slate-400 hover:text-slate-200 bg-dark-900 hover:bg-dark-800 border border-dark-700 transition disabled:opacity-50 cursor-pointer"
              title="Reset dropped counter to 0"
            >
              <RotateCcw className={`w-3.5 h-3.5 ${isResetting ? 'animate-spin' : ''}`} />
              <span>Reset counter to 0 ({currentCount.toLocaleString()})</span>
            </button>
          ) : (
            <div />
          )}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 rounded text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !hasFilterCriteria}
              className="px-3 py-1.5 rounded bg-accent-600 hover:bg-accent-500 text-white font-medium transition disabled:opacity-50 cursor-pointer"
            >
              {isSubmitting
                ? ruleToEdit
                  ? 'Saving...'
                  : 'Creating...'
                : ruleToEdit
                ? 'Save Changes'
                : 'Create Rule'}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
};

