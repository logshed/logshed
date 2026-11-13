import React from 'react';
import { Modal } from '../common/Modal.tsx';
import { PresetCard } from './PresetCard.tsx';
import { PresetCategoryFilter, usePresetCategoryFilter } from './PresetCategoryFilter.tsx';
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
  const { category, setCategory, options, visible } = usePresetCategoryFilter(presets, isOpen);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Drop Presets Library"
      maxWidth="max-w-4xl"
    >
      <div className="space-y-4">
        {/* Intro */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-line-muted">
          <div>
            <p className="text-xs text-fg-soft font-medium">Common Noise Filter Presets</p>
            <p className="text-xs text-fg-muted mt-0.5">
              1-click deployment of pre-configured drop rules for high-volume logs and background chatter.
            </p>
          </div>
          {presets.length > 0 && (
            <PresetCategoryFilter idPrefix="drop-presets" value={category} options={options} onChange={setCategory} />
          )}
        </div>

        {/* Presets Grid */}
        {presets.length === 0 ? (
          <div className="py-8 text-center text-xs text-fg-muted">No drop presets found.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
            {visible.map((preset) => {
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
                    <span className="px-1.5 py-0.5 rounded-sm text-[10px] font-mono bg-raised text-fg-soft border border-line shrink-0">
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
                    <span className="bg-raised px-2 py-0.5 rounded-sm text-fg-soft border border-line font-mono">
                      app: {preset.app_pattern}
                    </span>
                  )}
                  {preset.source_pattern && (
                    <span className="bg-raised px-2 py-0.5 rounded-sm text-fg-soft border border-line font-mono">
                      host: {preset.source_pattern}
                    </span>
                  )}
                  <span className="bg-raised px-2 py-0.5 rounded-sm text-accent-400 border border-line font-mono truncate max-w-full">
                    pattern: {preset.message_pattern}
                  </span>
                  {preset.severity_threshold != null && (
                    <span className="bg-raised px-2 py-0.5 rounded-sm text-fg-soft border border-line font-mono">
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
