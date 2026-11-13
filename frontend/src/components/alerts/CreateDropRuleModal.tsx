import React, { useState, useEffect, useMemo } from 'react';
import { ExternalLink, Play, RotateCcw } from 'lucide-react';
import { DropRule } from '../../types.ts';
import { createDropRule, updateDropRule, resetDropRuleCounter, testDropRule } from '../../api/dropRules.ts';
import { fetchAliases } from '../../api/aliases.ts';
import { Modal } from '../common/Modal.tsx';
import { MultiSelectDropdown } from '../common/MultiSelectDropdown.tsx';
import { SelectDropdown, SelectOption } from '../common/SelectDropdown.tsx';
import { UnsavedChangesModal } from '../common/UnsavedChangesModal.tsx';
import { lessSevereLabel } from '../../utils/severityLabels.ts';
import { MESSAGE_PATTERN_MAX_LENGTH, NAME_MAX_LENGTH, RULE_FILTER_MAX_LENGTH } from '../../utils/fieldLimits.ts';
import { joinPatternList, splitPatternList } from '../../utils/patternList.ts';

const SEVERITY_RANGE_OPTIONS: SelectOption[] = [
  { value: '', label: 'Any severity' },
  ...[7, 6, 5, 4, 3].map((level) => ({ value: String(level), label: lessSevereLabel(level) })),
];

const SAMPLE_SEVERITY_OPTIONS: SelectOption[] = [
  { value: '', label: 'Unspecified' },
  ...['Debug', 'Info', 'Notice', 'Warning', 'Error', 'Critical', 'Alert', 'Emergency'].map((name, i) => ({
    value: String(7 - i),
    label: `${name} (${7 - i})`,
  })),
];


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
  const [sources, setSources] = useState<string[]>([]);
  const [apps, setApps] = useState<string[]>([]);
  const [severityThreshold, setSeverityThreshold] = useState<number | null>(null);
  const [isEnabled, setIsEnabled] = useState<boolean>(true);
  const [currentCount, setCurrentCount] = useState<number>(0);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isResetting, setIsResetting] = useState<boolean>(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState<boolean>(false);

  // Interactive tester states
  const [sampleMessage, setSampleMessage] = useState<string>('');
  const [sampleSource, setSampleSource] = useState<string>('');
  const [sampleApp, setSampleApp] = useState<string>('');
  const [sampleSeverity, setSampleSeverity] = useState<string>('');
  const [testResult, setTestResult] = useState<{ matched?: boolean; error?: string | null } | null>(null);
  const [isTesting, setIsTesting] = useState<boolean>(false);

  // Syslog host aliases (lowercased keys both ways), so the host list can show which IP is which alias
  const [hostAliases, setHostAliases] = useState<{ ipByAlias: Map<string, string>; aliasByIp: Map<string, string> }>(
    () => ({ ipByAlias: new Map(), aliasByIp: new Map() })
  );

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    fetchAliases()
      .then((list) => {
        if (cancelled) return;
        const ipByAlias = new Map<string, string>();
        const aliasByIp = new Map<string, string>();
        for (const a of list) {
          if (!a.alias || !a.ip) continue;
          ipByAlias.set(a.alias.trim().toLowerCase(), a.ip);
          aliasByIp.set(a.ip.trim().toLowerCase(), a.alias);
        }
        setHostAliases({ ipByAlias, aliasByIp });
      })
      .catch(() => {
        // Without the aliases the list simply shows no IP hints
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  const hostHint = (option: string): string | undefined => {
    const key = option.trim().toLowerCase();
    const ip = hostAliases.ipByAlias.get(key);
    if (ip) return ip;
    const alias = hostAliases.aliasByIp.get(key);
    return alias ? `alias ${alias}` : undefined;
  };

  const prevIsOpenRef = React.useRef<boolean>(false);
  const prevTargetKeyRef = React.useRef<string>('');

  const currentTargetKey = ruleToEdit
    ? `rule:${ruleToEdit.id}`
    : `create:${initialSource}:${initialApp}:${initialMessage}:${initialName}`;

  // Initialize form state only on open or when target rule / initial values change
  useEffect(() => {
    const wasOpen = prevIsOpenRef.current;
    prevIsOpenRef.current = isOpen;
    const targetChanged = prevTargetKeyRef.current !== currentTargetKey;

    if (isOpen && (!wasOpen || targetChanged)) {
      prevTargetKeyRef.current = currentTargetKey;
      setTestResult(null);
      if (ruleToEdit) {
        setRuleName(ruleToEdit.name || ruleToEdit.app_pattern || ruleToEdit.source_pattern || '');
        setMessagePattern(ruleToEdit.message_pattern || '');
        setIsRegex(ruleToEdit.is_regex || false);
        setSeverityThreshold(ruleToEdit.severity_threshold ?? null);
        setIsEnabled(ruleToEdit.is_enabled);
        setCurrentCount(ruleToEdit.dropped_count || 0);

        const editSources = splitPatternList(ruleToEdit.source_pattern);
        const editApps = splitPatternList(ruleToEdit.app_pattern);
        setSources(editSources);
        setApps(editApps);

        setSampleMessage(ruleToEdit.message_pattern || '');
        setSampleSource(editSources[0] || '');
        setSampleApp(editApps[0] || '');
        setSampleSeverity(ruleToEdit.severity_threshold != null ? String(ruleToEdit.severity_threshold) : '');
      } else {
        setRuleName(initialName || (initialApp ? `Drop ${initialApp} Chatter` : (initialSource ? `Drop ${initialSource}` : '')));
        setMessagePattern(initialMessage);
        setIsRegex(false);
        setSeverityThreshold(null);
        setIsEnabled(true);
        setCurrentCount(0);

        setSources(splitPatternList(initialSource));
        setApps(splitPatternList(initialApp));

        setSampleMessage(initialMessage);
        setSampleSource(initialSource);
        setSampleApp(initialApp);
        setSampleSeverity('');
      }
    } else if (!isOpen) {
      prevTargetKeyRef.current = '';
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fills the form on open or a new target (currentTargetKey stands for the rule and initial values), not on each new prop object, which would undo the user's edits
  }, [isOpen, currentTargetKey]);

  useEffect(() => {
    if (!isOpen) {
      setShowDiscardConfirm(false);
    }
  }, [isOpen]);

  const sourcePattern = joinPatternList(sources);
  const appPattern = joinPatternList(apps);
  const sourceListTooLong = sourcePattern.length > RULE_FILTER_MAX_LENGTH;
  const appListTooLong = appPattern.length > RULE_FILTER_MAX_LENGTH;

  const isDirty = useMemo(() => {
    if (!isOpen) return false;
    if (ruleToEdit) {
      const initialRuleName = ruleToEdit.name || ruleToEdit.app_pattern || ruleToEdit.source_pattern || '';
      return (
        ruleName !== initialRuleName ||
        messagePattern !== (ruleToEdit.message_pattern || '') ||
        isRegex !== (ruleToEdit.is_regex || false) ||
        sourcePattern !== joinPatternList(splitPatternList(ruleToEdit.source_pattern)) ||
        appPattern !== joinPatternList(splitPatternList(ruleToEdit.app_pattern)) ||
        severityThreshold !== (ruleToEdit.severity_threshold ?? null) ||
        isEnabled !== ruleToEdit.is_enabled
      );
    } else {
      const defaultName = initialName || (initialApp ? `Drop ${initialApp} Chatter` : (initialSource ? `Drop ${initialSource}` : ''));
      return (
        ruleName !== defaultName ||
        messagePattern !== (initialMessage || '') ||
        isRegex !== false ||
        sourcePattern !== joinPatternList(splitPatternList(initialSource)) ||
        appPattern !== joinPatternList(splitPatternList(initialApp)) ||
        severityThreshold !== null ||
        isEnabled !== true
      );
    }
  }, [
    isOpen,
    ruleToEdit,
    initialName,
    initialApp,
    initialSource,
    initialMessage,
    ruleName,
    messagePattern,
    isRegex,
    sourcePattern,
    appPattern,
    severityThreshold,
    isEnabled,
  ]);

  const handleCloseAttempt = () => {
    if (showDiscardConfirm) return;
    if (isDirty) {
      setShowDiscardConfirm(true);
    } else {
      onClose();
    }
  };

  const hasFilterCriteria = Boolean(
    messagePattern.trim() || sourcePattern || appPattern || severityThreshold !== null
  );

  const handleTestPattern = async () => {
    if (!hasFilterCriteria || !sampleMessage.trim()) return;
    try {
      setIsTesting(true);
      setTestResult(null);
      const res = await testDropRule({
        message_pattern: messagePattern.trim() || '*',
        is_regex: isRegex,
        source_pattern: sourcePattern || undefined,
        app_pattern: appPattern || undefined,
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
    if (!hasFilterCriteria || sourceListTooLong || appListTooLong) return;

    const effectivePattern = messagePattern.trim() || '*';
    try {
      setIsSubmitting(true);
      if (ruleToEdit) {
        const updated = await updateDropRule(ruleToEdit.id, {
          name: ruleName.trim(),
          message_pattern: effectivePattern,
          is_regex: isRegex,
          source_pattern: sourcePattern || null,
          app_pattern: appPattern || null,
          severity_threshold: severityThreshold,
          is_enabled: isEnabled,
        });
        onSuccess?.(updated);
      } else {
        const created = await createDropRule({
          name: ruleName.trim(),
          message_pattern: effectivePattern,
          is_regex: isRegex,
          source_pattern: sourcePattern || undefined,
          app_pattern: appPattern || undefined,
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
      onClose={handleCloseAttempt}
      title={ruleToEdit ? 'Edit Ingestion Drop Rule' : 'Create Ingestion Drop Rule'}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmit} className="space-y-4 text-xs font-sans">
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-fg-soft font-medium">
              Rule Name <span className="text-danger-400">*</span>
            </label>
            <a
              href="https://github.com/logshed/logshed/blob/main/docs/RULES_GUIDE.md#ingestion-drop-rules"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[10px] text-accent-400 hover:underline"
              title="Open Drop Rules Documentation"
            >
              <span>Rule Guide &amp; Examples</span>
              <ExternalLink className="w-2.5 h-2.5" />
            </a>
          </div>
          <input
            type="text"
            value={ruleName}
            onChange={(e) => setRuleName(e.target.value)}
            placeholder="e.g. CRON Chatter or Docker Healthchecks"
            maxLength={NAME_MAX_LENGTH}
            required
            autoFocus
            className="w-full bg-canvas border border-line rounded-sm px-3 py-1.5 text-fg focus:outline-hidden focus:border-accent-500"
          />
        </div>

        <div>
          <label className="block text-fg-soft font-medium mb-1">
            Message Pattern <span className="text-fg-muted font-normal text-[11px]">(Optional if Host or App is selected)</span>
          </label>
          <input
            type="text"
            value={messagePattern}
            onChange={(e) => setMessagePattern(e.target.value)}
            placeholder="e.g. DHCPACK, query\s+from\b, probe, or * to match all messages"
            maxLength={MESSAGE_PATTERN_MAX_LENGTH}
            className="w-full bg-canvas border border-line rounded-sm px-3 py-1.5 text-fg font-mono focus:outline-hidden focus:border-accent-500"
          />
          <span className="text-[11px] text-fg-muted mt-1 block">
            Case-insensitive match string. Use * or leave empty to drop all messages matching the selected host or app.
          </span>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="modal-is-regex-checkbox"
            checked={isRegex}
            onChange={(e) => setIsRegex(e.target.checked)}
            className="rounded-sm bg-canvas border-line text-accent-500 focus:ring-accent-500/20"
          />
          <label htmlFor="modal-is-regex-checkbox" className="text-fg-soft cursor-pointer">
            Interpret message pattern as Regular Expression
          </label>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {/* Host / IP Filter */}
          <div>
            <span id="modal-host-label" className="block text-fg-soft font-medium mb-1">
              Hosts or IPs (Optional)
            </span>
            <MultiSelectDropdown
              label="Host"
              placeholder="Any host or IP"
              options={availableSources}
              selected={sources}
              onChange={(next) => {
                setSources(next);
                if (!sampleSource.trim() && next.length > 0) {
                  setSampleSource(next[0]);
                }
              }}
              allowCustomInput={true}
              variant="form"
              labelledBy="modal-host-label"
              optionHint={hostHint}
            />
            {sourceListTooLong ? (
              <span role="alert" className="text-[11px] text-danger-400 mt-1 block">
                The host list is longer than {RULE_FILTER_MAX_LENGTH} characters. Remove some, or use a wildcard pattern.
              </span>
            ) : (
              <span className="text-[11px] text-fg-muted mt-1 block">
                Any one must match the alias or IP. Type a pattern such as 192.168.1.* or router* to add it.
              </span>
            )}
            {sources
              .filter((src) => hostAliases.aliasByIp.has(src.trim().toLowerCase()))
              .map((ip) => (
                <span key={ip} data-testid="host-ip-alias-note" className="text-[11px] text-fg-muted mt-1 block">
                  {ip} is the IP of {hostAliases.aliasByIp.get(ip.trim().toLowerCase())}, so this rule drops its logs either way.
                </span>
              ))}
          </div>

          {/* Application or Container Filter */}
          <div>
            <span id="modal-app-label" className="block text-fg-soft font-medium mb-1">
              Apps or Containers (Optional)
            </span>
            <MultiSelectDropdown
              label="App"
              placeholder="Any app or container"
              options={availableApps}
              selected={apps}
              onChange={(next) => {
                setApps(next);
                if (!sampleApp.trim() && next.length > 0) {
                  setSampleApp(next[0]);
                }
              }}
              allowCustomInput={true}
              variant="form"
              labelledBy="modal-app-label"
            />
            {appListTooLong ? (
              <span role="alert" className="text-[11px] text-danger-400 mt-1 block">
                The app list is longer than {RULE_FILTER_MAX_LENGTH} characters. Remove some, or use a wildcard pattern.
              </span>
            ) : (
              <span className="text-[11px] text-fg-muted mt-1 block">
                Any one must match. Type a pattern such as smartbulb* to add it.
              </span>
            )}
          </div>
        </div>

        <div>
          <label id="modal-severity-select-label" htmlFor="modal-severity-select" className="block text-fg-soft font-medium mb-1">
            Severity Range (Optional)
          </label>
          <SelectDropdown
            variant="panel"
            fieldClassName="bg-canvas border-line rounded-sm px-3 py-1.5 font-mono text-fg"
            id="modal-severity-select"
            labelledBy="modal-severity-select-label"
            label="Severity Range (Optional)"
            options={SEVERITY_RANGE_OPTIONS}
            value={severityThreshold === null ? '' : severityThreshold.toString()}
            onChange={(v) => setSeverityThreshold(v === '' ? null : Number(v))}
          />
          <span className="text-[11px] text-fg-muted mt-1 block">
            Only logs at this level or less severe are dropped. More severe logs are always kept. Leave blank to match every level.
          </span>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="modal-is-enabled-checkbox"
            checked={isEnabled}
            onChange={(e) => setIsEnabled(e.target.checked)}
            className="rounded-sm bg-canvas border-line text-accent-500 focus:ring-accent-500/20"
          />
          <label htmlFor="modal-is-enabled-checkbox" className="text-fg-soft cursor-pointer">
            {ruleToEdit ? 'Rule active' : 'Rule enabled immediately upon creation'}
          </label>
        </div>

        {/* Interactive Pattern Tester Section */}
        <div className="bg-canvas border border-line-subtle rounded-lg p-3 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-fg text-xs">Test Pattern Match</span>
            <button
              type="button"
              onClick={handleTestPattern}
              disabled={isTesting || !hasFilterCriteria || !sampleMessage.trim()}
              className="flex items-center gap-1 px-2 py-0.5 rounded-sm bg-raised hover:bg-raised-strong text-fg-soft border border-line transition disabled:opacity-50 cursor-pointer text-[11px]"
            >
              <Play className="w-3 h-3 text-accent-400" />
              <span>{isTesting ? 'Testing...' : 'Dry-Run Test'}</span>
            </button>
          </div>

          <div>
            <label className="block text-fg-muted text-[11px] mb-1">Sample Log Message</label>
            <textarea
              rows={2}
              value={sampleMessage}
              onChange={(e) => setSampleMessage(e.target.value)}
              placeholder="Paste a representative log line here..."
              className="w-full bg-surface border border-line-subtle rounded-sm px-2.5 py-1.5 text-fg font-mono text-xs focus:outline-hidden focus:border-accent-500"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div>
              <label className="block text-fg-muted text-[11px] mb-0.5">Sample Host / IP</label>
              <input
                type="text"
                value={sampleSource}
                onChange={(e) => setSampleSource(e.target.value)}
                placeholder="e.g. 192.168.1.50"
                className="w-full bg-surface border border-line-subtle rounded-sm px-2 py-1 text-fg font-mono text-xs focus:outline-hidden focus:border-accent-500"
              />
            </div>
            <div>
              <label className="block text-fg-muted text-[11px] mb-0.5">Sample App / Container</label>
              <input
                type="text"
                value={sampleApp}
                onChange={(e) => setSampleApp(e.target.value)}
                placeholder="e.g. dnsmasq"
                className="w-full bg-surface border border-line-subtle rounded-sm px-2 py-1 text-fg font-mono text-xs focus:outline-hidden focus:border-accent-500"
              />
            </div>
            <div>
              <label id="modal-sample-severity-select-label" htmlFor="modal-sample-severity-select" className="block text-fg-muted text-[11px] mb-0.5">Sample Severity</label>
              <SelectDropdown
                variant="panel"
                fieldClassName="bg-surface border-line-subtle rounded-sm px-2 py-1 font-mono text-fg"
                id="modal-sample-severity-select"
                labelledBy="modal-sample-severity-select-label"
                label="Sample Severity"
                options={SAMPLE_SEVERITY_OPTIONS}
                value={sampleSeverity}
                onChange={setSampleSeverity}
              />
            </div>
          </div>

          {!ruleToEdit && messagePattern.trim() !== '' && messagePattern.trim() === sampleMessage.trim() && (
            <div
              data-testid="whole-line-pattern-hint"
              className="p-2 rounded-sm border text-[11px] bg-warning-950/40 border-warning-800/60 text-warning-300"
            >
              Tip: remove numbers, times or IDs that change between lines from the Message Pattern, so later logs match too.
            </div>
          )}

          {testResult && (
            <div
              className={`p-2 rounded border text-xs flex items-center justify-between ${
                testResult.error
                  ? 'bg-danger-950/40 border-danger-800/60 text-danger-300'
                  : testResult.matched
                  ? 'bg-success-950/40 border-success-800/60 text-success-300'
                  : 'bg-warning-950/40 border-warning-800/60 text-warning-300'
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

        <div className="flex items-center justify-between gap-2 pt-2 border-t border-line-subtle">
          {ruleToEdit ? (
            <button
              type="button"
              onClick={handleResetInModal}
              disabled={isResetting || currentCount === 0}
              aria-label="Reset counter to 0"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-sm text-xs text-fg-muted hover:text-fg bg-surface hover:bg-raised border border-line transition disabled:opacity-50 cursor-pointer"
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
              onClick={handleCloseAttempt}
              className="px-3 py-1.5 rounded-sm text-fg-muted hover:text-fg hover:bg-raised transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !hasFilterCriteria || sourceListTooLong || appListTooLong}
              className="px-3 py-1.5 rounded-sm bg-accent-700 hover:bg-accent-600 text-on-fill font-medium transition disabled:opacity-50 cursor-pointer"
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

