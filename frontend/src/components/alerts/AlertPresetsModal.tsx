import React from 'react';
import { Sparkles } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';
import { PresetCard } from './PresetCard.tsx';
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
      title="Alert Presets Library"
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
                <PresetCard
                  key={preset.id}
                  name={preset.name}
                  isCustom={preset.is_custom}
                  badge={
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-amber-950/40 text-amber-300 border border-amber-800/40 shrink-0">
                      {preset.rule_type}
                    </span>
                  }
                  description={preset.description}
                  alreadyInstalled={alreadyInstalled}
                  installedText="Preset already installed"
                  notInstalledText="Instant 1-click activation"
                  isInstalling={isInstalling}
                  onInstall={() => onInstallPreset(preset)}
                >
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
                </PresetCard>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
};
