import React from 'react';
import { Plus, RefreshCw, Sparkles, CheckCircle2 } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';
import { AlertPreset, AlertRule, NotificationChannel } from '../../types.ts';

export interface AlertPresetsModalProps {
  isOpen: boolean;
  onClose: () => void;
  presets: AlertPreset[];
  rules: AlertRule[];
  channels: NotificationChannel[];
  presetChannelId: number | null;
  onSelectChannelId: (id: number | null) => void;
  onInstallPreset: (preset: AlertPreset) => Promise<void> | void;
  installingPresetId: string | null;
}

export const AlertPresetsModal: React.FC<AlertPresetsModalProps> = ({
  isOpen,
  onClose,
  presets,
  rules,
  channels,
  presetChannelId,
  onSelectChannelId,
  onInstallPreset,
  installingPresetId,
}) => {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Alert Presets Catalog"
      maxWidth="max-w-4xl"
    >
      <div className="space-y-4">
        {/* Intro and Channel Selector */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-dark-750">
          <div>
            <p className="text-xs text-slate-300 font-medium">Pre-tuned Monitoring Rules</p>
            <p className="text-xs text-slate-400 mt-0.5">
              Instant deployment with automated threat signature detection.
            </p>
          </div>

          {channels.length > 0 && (
            <div className="flex items-center gap-2 shrink-0">
              <label htmlFor="target-channel-select" className="text-xs text-slate-400">
                Target Channel:
              </label>
              <select
                id="target-channel-select"
                value={presetChannelId ?? ''}
                onChange={(e) => onSelectChannelId(e.target.value ? Number(e.target.value) : null)}
                className="bg-dark-800 border border-dark-700 text-slate-200 text-xs rounded-lg px-2.5 py-1.5 focus:outline-hidden focus:border-accent-500"
              >
                <option value="">All Enabled Channels</option>
                {channels.map((ch) => (
                  <option key={ch.id} value={ch.id}>
                    {ch.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Presets Grid */}
        {presets.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-400">No alert presets found.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
            {presets.map((preset) => {
              const alreadyInstalled = rules.some(
                (r) => r.name.toLowerCase() === preset.name.toLowerCase()
              );
              const isInstalling = installingPresetId === preset.id;

              return (
                <div
                  key={preset.id}
                  className="bg-dark-950/70 border border-dark-750 hover:border-dark-650 rounded-xl p-4 flex flex-col justify-between space-y-3 transition"
                >
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-xs font-semibold text-slate-100 truncate">{preset.name}</span>
                        {preset.is_custom && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-950/60 text-blue-300 border border-blue-800 shrink-0">
                            Custom
                          </span>
                        )}
                      </div>
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-amber-950/40 text-amber-300 border border-amber-800/40 shrink-0">
                        {preset.rule_type}
                      </span>
                    </div>

                    <p className="text-xs text-slate-400 leading-relaxed">{preset.description}</p>

                    <div className="pt-1 flex flex-wrap gap-1.5 text-[11px] text-slate-400">
                      {preset.filter_app && (
                        <span className="bg-dark-800 px-2 py-0.5 rounded text-slate-300 border border-dark-700 font-mono">
                          app: {preset.filter_app}
                        </span>
                      )}
                      {preset.match_pattern && (
                        <span className="bg-dark-800 px-2 py-0.5 rounded text-accent-400 border border-dark-700 font-mono truncate max-w-full">
                          pattern: {preset.match_pattern}
                        </span>
                      )}
                      <span className="bg-dark-800 px-2 py-0.5 rounded text-slate-300 border border-dark-700">
                        {preset.rule_type === 'rate'
                          ? `>= ${preset.threshold_count} logs/s in ${preset.window_seconds}s`
                          : `${preset.threshold_count} in ${preset.window_seconds}s`}
                      </span>
                      <span className="bg-dark-800 px-2 py-0.5 rounded text-slate-300 border border-dark-700">
                        cooldown: {preset.cooldown_seconds}s
                      </span>
                      {preset.ai_enrichment && (
                        <span className="bg-purple-950/40 text-purple-300 px-2 py-0.5 rounded border border-purple-800/40 flex items-center gap-1">
                          <Sparkles className="w-2.5 h-2.5" />
                          <span>AI Enrichment</span>
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="pt-3 border-t border-dark-800 flex items-center justify-between gap-2">
                    <span className="text-[11px] text-slate-500">
                      {alreadyInstalled ? (
                        <span className="flex items-center gap-1 text-emerald-400">
                          <CheckCircle2 className="w-3 h-3 shrink-0" />
                          <span>Preset already installed</span>
                        </span>
                      ) : (
                        'Instant 1-click activation'
                      )}
                    </span>
                    <button
                      type="button"
                      onClick={() => onInstallPreset(preset)}
                      disabled={isInstalling}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-accent-400 bg-accent-500/10 hover:bg-accent-500/20 border border-accent-500/30 transition cursor-pointer disabled:opacity-50 shrink-0"
                    >
                      {isInstalling ? (
                        <>
                          <RefreshCw className="w-3 h-3 animate-spin" />
                          <span>Installing...</span>
                        </>
                      ) : (
                        <>
                          <Plus className="w-3 h-3" />
                          <span>{alreadyInstalled ? 'Install Again' : 'Install Rule'}</span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
};
