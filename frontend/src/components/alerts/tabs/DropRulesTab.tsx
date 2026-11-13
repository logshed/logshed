import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  FilterX,
  Plus,
  Trash2,
  Edit2,
  RefreshCw,
  Zap,
  Download,
  Upload,
} from 'lucide-react';
import { DropPreset, DropRule } from '../../../types.ts';
import {
  fetchDropRules,
  updateDropRule,
  deleteDropRule,
  fetchDropPresets,
  installDropPreset,
  exportAllDropRules,
  exportSingleDropRule,
  importDropRules,
  reorderDropRules,
} from '../../../api/dropRules.ts';
import { fetchLogFacets } from '../../../api/logs.ts';
import { slugify } from '../../../utils/formatters.ts';
import { ConfirmDialog } from '../../common/ConfirmDialog.tsx';
import { useSerialReorder } from '../../../utils/useSerialReorder.ts';
import { useReorderableList } from '../../../utils/useReorderableList.ts';
import { downloadRuleExport, importRuleFile } from '../../../utils/ruleTransfer.ts';
import { lessSevereLabel } from '../../../utils/severityLabels.ts';
import { CreateDropRuleModal } from '../CreateDropRuleModal.tsx';
import { splitPatternList } from '../../../utils/patternList.ts';
import { DropPresetsModal } from '../DropPresetsModal.tsx';
import { RuleOrderControls } from '../RuleOrderControls.tsx';
import { useFlash } from '../../../utils/hooks.ts';

export interface DropRulesTabProps {
  onRulesChange?: (rules: DropRule[]) => void;
}


export const DropRulesTab: React.FC<DropRulesTabProps> = ({ onRulesChange }) => {
  const [rules, setRules] = useState<DropRule[]>([]);
  const [presets, setPresets] = useState<DropPreset[]>([]);
  const [isPresetsModalOpen, setIsPresetsModalOpen] = useState<boolean>(false);
  const [installingPresetId, setInstallingPresetId] = useState<string | null>(null);
  const [hasLoaded, setHasLoaded] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [ruleToEdit, setRuleToEdit] = useState<DropRule | null>(null);
  const [ruleToDelete, setRuleToDelete] = useState<DropRule | null>(null);
  // Success messages clear after 3 seconds; errors stay until replaced
  const [feedbackMsg, flashFeedback, setFeedbackMsg] = useFlash<{ text: string; isError: boolean }>(3000);
  const [availableSources, setAvailableSources] = useState<string[]>([]);
  const [availableApps, setAvailableApps] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadRules = useCallback(async () => {
    try {
      setIsLoading(true);
      const data = await fetchDropRules();
      setRules(data);
      setHasLoaded(true);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to load drop rules.', isError: true });
    } finally {
      setIsLoading(false);
    }
  }, [setFeedbackMsg]);

  const loadPresets = useCallback(async () => {
    try {
      const data = await fetchDropPresets();
      setPresets(data);
    } catch {
      // Ignore preset load error
    }
  }, []);

  useEffect(() => {
    loadRules();
    loadPresets();
    fetchLogFacets()
      .then((res) => {
        setAvailableSources(res.sources || []);
        setAvailableApps(res.apps || []);
      })
      .catch(() => {});
  }, [loadRules, loadPresets]);

  useEffect(() => {
    if (!hasLoaded) return;
    onRulesChange?.(rules);
  }, [rules, hasLoaded, onRulesChange]);

  const handleReorder = useSerialReorder<DropRule>(
    rules,
    setRules,
    reorderDropRules,
    (message) => setFeedbackMsg({ text: message, isError: true })
  );
  const { draggedIndex, moveItem, dragProps } = useReorderableList(rules, handleReorder);

  const handleInstallPreset = async (preset: DropPreset) => {
    setInstallingPresetId(preset.id);
    try {
      const installed = await installDropPreset(preset.id);
      setRules((prev) => [...prev, installed]);
      flashFeedback({ text: `Drop preset "${preset.name}" installed.`, isError: false });
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to install drop preset.', isError: true });
    } finally {
      setInstallingPresetId(null);
    }
  };

  const handleToggleStatus = async (rule: DropRule) => {
    try {
      const updated = await updateDropRule(rule.id, { is_enabled: !rule.is_enabled });
      setRules((prev) => prev.map((r) => (r.id === rule.id ? updated : r)));
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to update rule status.', isError: true });
    }
  };

  const handleDeleteRule = async (ruleId: number) => {
    try {
      await deleteDropRule(ruleId);
      setRules((prev) => prev.filter((r) => r.id !== ruleId));
      flashFeedback({ text: 'Drop rule deleted.', isError: false });
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to delete drop rule.', isError: true });
    }
  };

  const handleExportAll = () =>
    downloadRuleExport(exportAllDropRules, 'logshed-drop-rules.json', setFeedbackMsg, 'Failed to export drop rules.');

  const handleExportSingle = (rule: DropRule) =>
    downloadRuleExport(
      () => exportSingleDropRule(rule.id),
      rule.name ? `${slugify(rule.name)}.json` : `drop-rule-${rule.id}.json`,
      setFeedbackMsg,
      'Failed to export drop rule.'
    );

  const handleFileImport = (e: React.ChangeEvent<HTMLInputElement>) =>
    importRuleFile(e, importDropRules, setFeedbackMsg, loadRules);

  const totalDropped = rules.reduce((acc, r) => acc + (r.dropped_count || 0), 0);

  return (
    <div className="bg-surface border border-line rounded-xl overflow-hidden shadow-xs">
      {/* Header */}
      <div className="px-5 py-4 border-b border-line flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h3 className="text-xs font-semibold text-fg-soft uppercase tracking-wider flex items-center gap-2">
            <FilterX className="w-4 h-4 text-accent-500" />
            <span>Ingestion Drop Rules</span>
          </h3>
          <p className="text-xs text-fg-muted mt-0.5">
            Discard repetitive syslog or container chatter
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 self-start sm:self-auto sm:shrink-0">
          {totalDropped > 0 && (
            <span className="text-[11px] px-1.5 py-0.5 rounded-sm bg-success-950/60 border border-success-800 text-success-300 font-mono whitespace-nowrap">
              {totalDropped.toLocaleString()} dropped
            </span>
          )}

          <button
            type="button"
            onClick={loadRules}
            disabled={isLoading}
            className="p-2 rounded-lg text-fg-muted hover:text-fg-max bg-raised hover:bg-raised-hover border border-line transition cursor-pointer disabled:opacity-50"
            title="Refresh rules"
            aria-label="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => setIsPresetsModalOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-fg-soft bg-raised hover:bg-raised-hover hover:text-fg-max border border-line transition cursor-pointer whitespace-nowrap"
            title="Browse and install pre-configured drop rule presets"
          >
            <Zap className="w-3.5 h-3.5 text-accent-500" />
            <span>Presets</span>
          </button>

          <button
            type="button"
            onClick={handleExportAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-fg-soft bg-raised hover:bg-raised-hover hover:text-fg-max border border-line transition cursor-pointer whitespace-nowrap"
            title="Export all drop rules"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Export All</span>
          </button>

          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileImport}
            accept=".json,application/json"
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-fg-soft bg-raised hover:bg-raised-hover hover:text-fg-max border border-line transition cursor-pointer whitespace-nowrap"
            title="Import drop rules from JSON file"
          >
            <Upload className="w-3.5 h-3.5" />
            <span>Import</span>
          </button>

          <button
            type="button"
            onClick={() => {
              setRuleToEdit(null);
              setIsModalOpen(true);
            }}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-on-fill bg-accent-700 hover:bg-accent-600 transition shadow-xs cursor-pointer whitespace-nowrap"
            title="Create a new ingestion drop rule"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>New Rule</span>
          </button>
        </div>
      </div>

      {feedbackMsg && (
        <div
          className={`m-4 p-2.5 rounded-lg border text-xs flex items-center justify-between ${
            feedbackMsg.isError
              ? 'bg-danger-950/60 border-danger-800 text-danger-300'
              : 'bg-success-950/60 border-success-800 text-success-300'
          }`}
        >
          <span>{feedbackMsg.text}</span>
          <button onClick={() => setFeedbackMsg(null)} className="text-fg-muted hover:text-fg cursor-pointer">
            &times;
          </button>
        </div>
      )}

      {rules.length === 0 ? (
        <div className="p-10 text-center space-y-3">
          <div className="p-3 bg-raised text-fg-muted rounded-full w-12 h-12 mx-auto flex items-center justify-center">
            <FilterX className="w-6 h-6" />
          </div>
          <p className="text-xs text-fg-soft font-medium">No drop rules configured yet.</p>
          <p className="text-xs text-fg-muted max-w-md mx-auto">
            Create a drop rule to filter noisy devices or repetitive syslog chatter at ingestion.
          </p>
          <div className="flex flex-wrap justify-center gap-2 pt-2">
            <button
              type="button"
              onClick={() => setIsPresetsModalOpen(true)}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-fg-soft bg-raised hover:bg-raised-hover hover:text-fg-max border border-line transition cursor-pointer"
            >
              <Zap className="w-3.5 h-3.5 text-accent-500" />
              <span>Browse Presets</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setRuleToEdit(null);
                setIsModalOpen(true);
              }}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-on-fill bg-accent-700 hover:bg-accent-600 transition cursor-pointer"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Create Drop Rule</span>
            </button>
          </div>
        </div>
      ) : (
        <div className="divide-y divide-line-subtle">
          {rules.map((rule, index) => {
            const ruleDisplayName = rule.name || rule.app_pattern || rule.source_pattern || 'Drop Rule';
            return (
              <div
                key={rule.id}
                data-testid={`drop-rule-row-${rule.id}`}
                {...dragProps(index)}
                className={`p-4 transition flex flex-col md:flex-row md:items-center justify-between gap-4 ${
                  draggedIndex === index ? 'opacity-40 bg-raised/60' : ''
                } ${
                  rule.is_enabled ? 'hover:bg-surface-hover/40' : 'opacity-60 bg-canvas/20'
                }`}
              >
                <div className="flex items-start gap-3 min-w-0 flex-1">
                  <RuleOrderControls ruleId={rule.id} index={index} count={rules.length} onMove={moveItem} />

                  <div className="space-y-1.5 min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium text-fg">{ruleDisplayName}</span>
                      <span className="px-1.5 py-0.5 rounded-sm text-[10px] font-mono uppercase bg-raised text-fg-soft border border-line-hover">
                        {rule.is_regex ? 'Regex' : 'Substring'}
                      </span>
                      {(rule.dropped_count || 0) > 0 && (
                        <span className="px-1.5 py-0.5 rounded-sm text-[10px] font-mono bg-success-950/40 text-success-300 border border-success-800/40">
                          {(rule.dropped_count || 0).toLocaleString()} dropped
                        </span>
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
                      <span className="wrap-break-word min-w-0">
                        {splitPatternList(rule.source_pattern).length > 1 ? 'Hosts' : 'Host'}:{' '}
                        {rule.source_pattern ? (
                          <span className="font-mono text-fg-soft">{rule.source_pattern}</span>
                        ) : (
                          <span className="text-fg-muted italic">Any</span>
                        )}
                      </span>
                      <span className="wrap-break-word min-w-0">
                        {splitPatternList(rule.app_pattern).length > 1 ? 'Apps' : 'App'}:{' '}
                        {rule.app_pattern ? (
                          <span className="font-mono text-fg-soft">{rule.app_pattern}</span>
                        ) : (
                          <span className="text-fg-muted italic">Any</span>
                        )}
                      </span>
                      <span className="flex items-baseline gap-1 min-w-0 max-w-full">
                        <span className="shrink-0">Pattern:</span>
                        {rule.message_pattern && rule.message_pattern !== '*' ? (
                          <span
                            className="font-mono text-fg-soft truncate max-w-[16rem] sm:max-w-xs"
                            title={rule.message_pattern}
                          >
                            {rule.message_pattern}
                          </span>
                        ) : (
                          <span className="text-fg-muted italic">Any</span>
                        )}
                      </span>
                      <span>
                        Severity:{' '}
                        <span className="text-fg-soft">
                          {rule.severity_threshold != null
                            ? lessSevereLabel(rule.severity_threshold)
                            : 'Any'}
                        </span>
                      </span>
                    </div>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2.5 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleToggleStatus(rule)}
                    className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer ${
                      rule.is_enabled
                        ? 'bg-success-950 text-success-400 border border-success-800'
                        : 'bg-raised text-fg-muted border border-line'
                    }`}
                    title={rule.is_enabled ? 'Click to disable' : 'Click to enable'}
                  >
                    {rule.is_enabled ? 'Active' : 'Disabled'}
                  </button>

                  <button
                    type="button"
                    onClick={() => handleExportSingle(rule)}
                    aria-label={`Export rule ${rule.id}`}
                    className="p-1.5 text-fg-muted hover:text-fg hover:bg-raised rounded-sm transition cursor-pointer"
                    title="Export rule"
                  >
                    <Download className="w-3.5 h-3.5" />
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setRuleToEdit(rule);
                      setIsModalOpen(true);
                    }}
                    aria-label={`Edit rule ${rule.id}`}
                    className="p-1.5 text-fg-muted hover:text-fg hover:bg-raised rounded-sm transition cursor-pointer"
                    title="Edit rule"
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                  </button>

                  <button
                    type="button"
                    onClick={() => setRuleToDelete(rule)}
                    aria-label={`Delete rule ${rule.id}`}
                    className="p-1.5 text-fg-muted hover:text-danger-400 hover:bg-danger-950/20 rounded-sm transition cursor-pointer"
                    title="Delete rule"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Delete Rule Confirmation Modal */}
      {ruleToDelete && (
        <ConfirmDialog
          isOpen={!!ruleToDelete}
          title="Delete Ingestion Drop Rule"
          look="notice"
          onCancel={() => setRuleToDelete(null)}
          onConfirm={async () => {
            const id = ruleToDelete.id;
            setRuleToDelete(null);
            await handleDeleteRule(id);
          }}
          confirmIcon={<Trash2 className="w-3.5 h-3.5" />}
          confirmLabel="Delete Rule"
        >
          <p className="font-semibold text-fg-strong text-xs">
            Delete this drop rule?
          </p>
          <p className="text-fg-muted leading-relaxed">
            Incoming matching logs will no longer be discarded and will resume being stored in SQLite and indexed by FTS5.
          </p>
          <div className="p-2.5 rounded-sm bg-canvas/80 border border-line font-mono text-[11px] text-fg-soft space-y-1">
            <div>
              <span className="text-fg-muted">Name:</span> {ruleToDelete.name || ruleToDelete.app_pattern || ruleToDelete.source_pattern || 'Drop Rule'}
            </div>
            {ruleToDelete.source_pattern && (
              <div><span className="text-fg-muted">Host:</span> {ruleToDelete.source_pattern}</div>
            )}
            {ruleToDelete.app_pattern && (
              <div><span className="text-fg-muted">App:</span> {ruleToDelete.app_pattern}</div>
            )}
            {ruleToDelete.message_pattern && (
              <div><span className="text-fg-muted">Pattern:</span> {ruleToDelete.message_pattern}</div>
            )}
            {ruleToDelete.severity_threshold != null && (
              <div><span className="text-fg-muted">Severity:</span> {lessSevereLabel(ruleToDelete.severity_threshold)}</div>
            )}
            {ruleToDelete.dropped_count > 0 && (
              <div className="text-fg-muted text-[11px] pt-1 border-t border-line-subtle">
                Total logs dropped by this rule: {ruleToDelete.dropped_count.toLocaleString()}
              </div>
            )}
          </div>
        </ConfirmDialog>
      )}

      {/* Create / Edit Rule Modal with Interactive Pattern Tester */}
      <CreateDropRuleModal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          setRuleToEdit(null);
        }}
        ruleToEdit={ruleToEdit}
        availableSources={availableSources}
        availableApps={availableApps}
        onSuccess={(savedRule) => {
          setRules((prev) => {
            const exists = prev.some((r) => r.id === savedRule.id);
            if (exists) {
              return prev.map((r) => (r.id === savedRule.id ? savedRule : r));
            }
            return [...prev, savedRule];
          });
          flashFeedback({
            text: ruleToEdit ? 'Drop rule updated successfully.' : 'Drop rule created successfully.',
            isError: false,
          });
        }}
      />

      {/* Drop Presets Modal */}
      <DropPresetsModal
        isOpen={isPresetsModalOpen}
        onClose={() => setIsPresetsModalOpen(false)}
        presets={presets}
        rules={rules}
        onInstallPreset={handleInstallPreset}
        installingPresetId={installingPresetId}
      />
    </div>
  );
};

export default DropRulesTab;
