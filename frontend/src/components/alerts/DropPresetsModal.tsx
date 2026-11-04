import React from 'react';
import { Modal } from '../common/Modal.tsx';
import { PresetCard } from './PresetCard.tsx';
import { DropPreset, DropRule } from '../../types.ts';
import { lessSevereLabel } from '../../utils/severityLabels.ts';

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
      title="Drop Presets Library"
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
                <PresetCard
                  key={preset.id}
                  name={preset.name}
                  isCustom={preset.is_custom}
                  badge={
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-dark-800 text-slate-300 border border-dark-700 shrink-0">
                      {preset.is_regex ? 'Regex' : 'Substring'}
                    </span>
                  }
                  description={preset.description}
                  alreadyInstalled={alreadyInstalled}
                  installedText="Rule already configured"
                  notInstalledText="Instant 1-click filter"
                  isInstalling={isInstalling}
                  onInstall={() => onInstallPreset(preset)}
                >
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
                      {lessSevereLabel(preset.severity_threshold)}
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
