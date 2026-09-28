import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  FilterX,
  Plus,
  Trash2,
  Edit2,
  RefreshCw,
  AlertTriangle,
  Zap,
  Download,
  Upload,
} from 'lucide-react';
import { DropPreset, DropRule } from '../../types.ts';
import {
  fetchDropRules,
  updateDropRule,
  deleteDropRule,
  fetchDropPresets,
  installDropPreset,
  exportAllDropRules,
  exportSingleDropRule,
  importDropRules,
} from '../../api/dropRules.ts';
import { fetchLogFacets } from '../../api/logs.ts';
import { downloadBlob, slugify } from '../../utils/formatters.ts';
import { Modal } from '../common/Modal.tsx';
import { getSeverityInfo } from '../common/SeverityBadge.tsx';
import { CreateDropRuleModal } from './CreateDropRuleModal.tsx';
import { DropPresetsModal } from './DropPresetsModal.tsx';

export interface DropRulesCardProps {
  onRulesChange?: (rules: DropRule[]) => void;
}

export const DropRulesCard: React.FC<DropRulesCardProps> = ({ onRulesChange }) => {
  const [rules, setRules] = useState<DropRule[]>([]);
  const [presets, setPresets] = useState<DropPreset[]>([]);
  const [isPresetsModalOpen, setIsPresetsModalOpen] = useState<boolean>(false);
  const [installingPresetId, setInstallingPresetId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [ruleToEdit, setRuleToEdit] = useState<DropRule | null>(null);
  const [ruleToDelete, setRuleToDelete] = useState<DropRule | null>(null);
  const [feedbackMsg, setFeedbackMsg] = useState<{ text: string; isError: boolean } | null>(null);
  const [availableSources, setAvailableSources] = useState<string[]>([]);
  const [availableApps, setAvailableApps] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadRules = useCallback(async () => {
    try {
      setIsLoading(true);
      const data = await fetchDropRules();
      setRules(data);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to load drop rules.', isError: true });
    } finally {
      setIsLoading(false);
    }
  }, []);

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
    onRulesChange?.(rules);
  }, [rules, onRulesChange]);

  const handleInstallPreset = async (preset: DropPreset) => {
    setInstallingPresetId(preset.id);
    try {
      const installed = await installDropPreset(preset.id);
      setRules((prev) => [...prev, installed]);
      setFeedbackMsg({ text: `Drop preset "${preset.name}" installed.`, isError: false });
      setTimeout(() => setFeedbackMsg(null), 3000);
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
      setFeedbackMsg({ text: 'Drop rule deleted.', isError: false });
      setTimeout(() => setFeedbackMsg(null), 3000);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to delete drop rule.', isError: true });
    }
  };

  const handleExportAll = async () => {
    try {
      const blob = await exportAllDropRules();
      downloadBlob(blob, 'logshed-drop-rules.json');
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to export drop rules.', isError: true });
    }
  };

  const handleExportSingle = async (rule: DropRule) => {
    try {
      const blob = await exportSingleDropRule(rule.id);
      const filename = rule.name ? `${slugify(rule.name)}.json` : `drop-rule-${rule.id}.json`;
      downloadBlob(blob, filename);
    } catch (err: any) {
      setFeedbackMsg({ text: err.message || 'Failed to export drop rule.', isError: true });
    }
  };

  const handleFileImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = '';

    const reader = new FileReader();
    reader.onload = async (event) => {
      try {
        const text = event.target?.result as string;
        const json = JSON.parse(text);
        const res = await importDropRules(json);
        if (res.errors && res.errors.length > 0) {
          setFeedbackMsg({
            text: `Imported ${res.imported}, skipped ${res.skipped}. Errors: ${res.errors.join('; ')}`,
            isError: res.imported === 0,
          });
        } else {
          setFeedbackMsg({
            text: `Imported ${res.imported} rule${res.imported === 1 ? '' : 's'} (${res.skipped} skipped).`,
            isError: false,
          });
        }
        loadRules();
      } catch (err: any) {
        setFeedbackMsg({
          text: err.message || 'Failed to import rules. Invalid JSON file.',
          isError: true,
        });
      }
    };
    reader.onerror = () => {
      setFeedbackMsg({
        text: 'Failed to read file.',
        isError: true,
      });
    };
    reader.readAsText(file);
  };

  const totalDropped = rules.reduce((acc, r) => acc + (r.dropped_count || 0), 0);

  return (
    <div className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-xs">
      {/* Header */}
      <div className="px-5 py-4 border-b border-dark-700 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <FilterX className="w-4 h-4 text-accent-500" />
            <span>Ingestion Drop Rules</span>
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Discard repetitive syslog or container chatter
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 self-start sm:self-auto sm:shrink-0">
          {totalDropped > 0 && (
            <span className="text-[11px] px-1.5 py-0.5 rounded bg-emerald-950/60 border border-emerald-800 text-emerald-300 font-mono whitespace-nowrap">
              {totalDropped.toLocaleString()} dropped
            </span>
          )}

          <button
            type="button"
            onClick={loadRules}
            disabled={isLoading}
            className="p-2 rounded-lg text-slate-400 hover:text-white bg-dark-800 hover:bg-dark-750 border border-dark-700 transition cursor-pointer disabled:opacity-50"
            title="Refresh rules"
            aria-label="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => setIsPresetsModalOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer whitespace-nowrap"
            title="Browse and install pre-configured drop rule presets"
          >
            <Zap className="w-3.5 h-3.5 text-accent-500" />
            <span>Presets</span>
          </button>

          <button
            type="button"
            onClick={handleExportAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer whitespace-nowrap"
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
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer whitespace-nowrap"
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
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 transition shadow-xs cursor-pointer whitespace-nowrap"
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
              ? 'bg-red-950/60 border-red-800 text-red-300'
              : 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
          }`}
        >
          <span>{feedbackMsg.text}</span>
          <button onClick={() => setFeedbackMsg(null)} className="text-slate-400 hover:text-slate-200 cursor-pointer">
            &times;
          </button>
        </div>
      )}

      {rules.length === 0 ? (
        <div className="p-10 text-center space-y-3">
          <div className="p-3 bg-dark-800 text-slate-400 rounded-full w-12 h-12 mx-auto flex items-center justify-center">
            <FilterX className="w-6 h-6" />
          </div>
          <p className="text-xs text-slate-300 font-medium">No drop rules configured yet.</p>
          <p className="text-xs text-slate-500 max-w-md mx-auto">
            Create a drop rule to filter noisy devices or repetitive syslog chatter at ingestion.
          </p>
          <div className="flex flex-wrap justify-center gap-2 pt-2">
            <button
              type="button"
              onClick={() => setIsPresetsModalOpen(true)}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 hover:text-white border border-dark-700 transition cursor-pointer"
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
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 transition cursor-pointer"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Create Drop Rule</span>
            </button>
          </div>
        </div>
      ) : (
        <div className="divide-y divide-dark-800">
          {rules.map((rule) => {
            const ruleDisplayName = rule.name || rule.app_pattern || rule.source_pattern || 'Drop Rule';
            return (
              <div
                key={rule.id}
                data-testid={`drop-rule-row-${rule.id}`}
                className={`p-4 transition flex flex-col md:flex-row md:items-center justify-between gap-4 ${
                  rule.is_enabled ? 'hover:bg-dark-850/40' : 'opacity-60 bg-dark-950/20'
                }`}
              >
                <div className="space-y-1.5 min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium text-slate-200">{ruleDisplayName}</span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono uppercase bg-dark-800 text-slate-300 border border-dark-650">
                      {rule.is_regex ? 'Regex' : 'Substring'}
                    </span>
                    {(rule.dropped_count || 0) > 0 && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-950/40 text-emerald-300 border border-emerald-800/40">
                        {(rule.dropped_count || 0).toLocaleString()} dropped
                      </span>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                    <span>
                      Host:{' '}
                      {rule.source_pattern ? (
                        <span className="font-mono text-slate-300">{rule.source_pattern}</span>
                      ) : (
                        <span className="text-slate-400 italic">Any</span>
                      )}
                    </span>
                    <span>
                      App:{' '}
                      {rule.app_pattern ? (
                        <span className="font-mono text-slate-300">{rule.app_pattern}</span>
                      ) : (
                        <span className="text-slate-400 italic">Any</span>
                      )}
                    </span>
                    <span>
                      Severity:{' '}
                      <span className="text-slate-300">
                        {rule.severity_threshold != null
                          ? `${getSeverityInfo(rule.severity_threshold).label} and below`
                          : 'Any'}
                      </span>
                    </span>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2.5 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleToggleStatus(rule)}
                    className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider transition cursor-pointer ${
                      rule.is_enabled
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                        : 'bg-dark-800 text-slate-400 border border-dark-700'
                    }`}
                    title={rule.is_enabled ? 'Click to disable' : 'Click to enable'}
                  >
                    {rule.is_enabled ? 'Active' : 'Disabled'}
                  </button>

                  <button
                    type="button"
                    onClick={() => handleExportSingle(rule)}
                    aria-label={`Export rule ${rule.id}`}
                    className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
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
                    className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                    title="Edit rule"
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                  </button>

                  <button
                    type="button"
                    onClick={() => setRuleToDelete(rule)}
                    aria-label={`Delete rule ${rule.id}`}
                    className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-red-950/20 rounded transition cursor-pointer"
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
        <Modal
          isOpen={!!ruleToDelete}
          onClose={() => setRuleToDelete(null)}
          title="Delete Ingestion Drop Rule"
          maxWidth="max-w-md"
        >
          <div className="space-y-4 text-xs font-sans">
            <div className="flex items-start gap-3 p-3 bg-red-950/30 border border-red-800/60 rounded-lg text-slate-200">
              <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
              <div className="space-y-2 flex-1">
                <p className="font-semibold text-slate-100 text-xs">
                  Delete this drop rule?
                </p>
                <p className="text-slate-400 leading-relaxed">
                  Incoming matching logs will no longer be discarded and will resume being stored in SQLite and indexed by FTS5.
                </p>
                <div className="p-2.5 rounded bg-dark-950/80 border border-dark-700 font-mono text-[11px] text-slate-300 space-y-1">
                  <div>
                    <span className="text-slate-500">Name:</span> {ruleToDelete.name || ruleToDelete.app_pattern || ruleToDelete.source_pattern || 'Drop Rule'}
                  </div>
                  {ruleToDelete.source_pattern && (
                    <div><span className="text-slate-500">Host:</span> {ruleToDelete.source_pattern}</div>
                  )}
                  {ruleToDelete.app_pattern && (
                    <div><span className="text-slate-500">App:</span> {ruleToDelete.app_pattern}</div>
                  )}
                  {ruleToDelete.message_pattern && (
                    <div><span className="text-slate-500">Pattern:</span> {ruleToDelete.message_pattern}</div>
                  )}
                  {ruleToDelete.severity_threshold != null && (
                    <div><span className="text-slate-500">Severity:</span> {getSeverityInfo(ruleToDelete.severity_threshold).label} and below</div>
                  )}
                  {ruleToDelete.dropped_count > 0 && (
                    <div className="text-slate-400 text-[10px] pt-1 border-t border-dark-800">
                      Total logs dropped by this rule: {ruleToDelete.dropped_count.toLocaleString()}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setRuleToDelete(null)}
                className="px-3 py-1.5 text-xs bg-dark-800 hover:bg-dark-700 text-slate-300 border border-dark-600 rounded-lg transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={async () => {
                  const id = ruleToDelete.id;
                  setRuleToDelete(null);
                  await handleDeleteRule(id);
                }}
                className="flex items-center gap-1.5 px-3.5 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg shadow transition cursor-pointer"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Delete Rule</span>
              </button>
            </div>
          </div>
        </Modal>
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
          setFeedbackMsg({
            text: ruleToEdit ? 'Drop rule updated successfully.' : 'Drop rule created successfully.',
            isError: false,
          });
          setTimeout(() => setFeedbackMsg(null), 3000);
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
