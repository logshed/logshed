import React from 'react';
import { Plus, RefreshCw, CheckCircle2 } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';
import { DropPreset, DropRule } from '../../types.ts';
import { getSeverityInfo } from '../common/SeverityBadge.tsx';

export interface DropPresetsModalProps {
  isOpen: boolean;
  onClose: () => void;
  presets: DropPreset[];
  rules: DropRule[];
  onInstallPreset: (preset: DropPreset) => Promise<void> | void;
  installingPresetId: string | null;
}

export const DropPresetsModal: React.FC<DropPresetsModalProps> = ({
  isOpen,
  onClose,
  presets,
  rules,
  onInstallPreset,
  installingPresetId,
}) => {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Drop Presets Catalog"
      maxWidth="max-w-4xl"
    >
      <div className="space-y-4">
        {/* Intro */}
        <div className="pb-3 border-b border-dark-750">
          <p className="text-xs text-slate-300 font-medium">Common Noise Filter Presets</p>
          <p className="text-xs text-slate-400 mt-0.5">
            1-click deployment of pre-configured drop rules for high-volume logs and background chatter.
          </p>
        </div>

        {/* Presets Grid */}
        {presets.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-400">No drop presets found.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
            {presets.map((preset) => {
              const alreadyInstalled = rules.some(
                (r) =>
                  r.message_pattern === preset.message_pattern &&
                  r.app_pattern === preset.app_pattern
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
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-dark-800 text-slate-300 border border-dark-700 shrink-0">
                        {preset.is_regex ? 'Regex' : 'Substring'}
                      </span>
                    </div>

                    <p className="text-xs text-slate-400 leading-relaxed">{preset.description}</p>

                    <div className="pt-1 flex flex-wrap gap-1.5 text-[11px] text-slate-400">
                      {preset.app_pattern && (
                        <span className="bg-dark-800 px-2 py-0.5 rounded text-slate-300 border border-dark-700 font-mono">
                          app: {preset.app_pattern}
                        </span>
                      )}
                      {preset.source_pattern && (
                        <span className="bg-dark-800 px-2 py-0.5 rounded text-slate-300 border border-dark-700 font-mono">
                          host: {preset.source_pattern}
                        </span>
                      )}
                      <span className="bg-dark-800 px-2 py-0.5 rounded text-accent-400 border border-dark-700 font-mono truncate max-w-full">
                        pattern: {preset.message_pattern}
                      </span>
                      {preset.severity_threshold != null && (
                        <span className="bg-dark-800 px-2 py-0.5 rounded text-slate-300 border border-dark-700 font-mono">
                          {getSeverityInfo(preset.severity_threshold).label} and below
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="pt-3 border-t border-dark-800 flex items-center justify-between gap-2">
                    <span className="text-[11px] text-slate-500">
                      {alreadyInstalled ? (
                        <span className="flex items-center gap-1 text-emerald-400">
                          <CheckCircle2 className="w-3 h-3 shrink-0" />
                          <span>Rule already configured</span>
                        </span>
                      ) : (
                        'Instant 1-click filter'
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
