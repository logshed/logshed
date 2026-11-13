import React from 'react';
import { Sparkles } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';
import { SelectDropdown } from '../common/SelectDropdown.tsx';
import { PresetCard } from './PresetCard.tsx';
import { PresetCategoryFilter, usePresetCategoryFilter } from './PresetCategoryFilter.tsx';
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
  const { category, setCategory, options, visible } = usePresetCategoryFilter(presets, isOpen);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Alert Presets Library"
      maxWidth="max-w-4xl"
    >
      <div className="space-y-4">
        {/* Intro, Category Filter and Channel Selector */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-line-subtle">
          <div>
            <p className="text-xs text-fg-soft font-medium">Pre-tuned Monitoring Rules</p>
            <p className="text-xs text-fg-muted mt-0.5">
              Ready-made rules for failing disks, backups, power, network trouble and security events.
            </p>
          </div>

          <div className="flex flex-col lg:flex-row lg:items-center gap-2 shrink-0">
            {presets.length > 0 && (
              <PresetCategoryFilter idPrefix="alert-presets" value={category} options={options} onChange={setCategory} />
            )}
            {channels.length > 0 && (
              <div className="flex items-center gap-2 shrink-0">
                <label id="target-channel-select-label" htmlFor="target-channel-select" className="text-xs text-fg-muted w-24 sm:w-auto">
                  Target Channel:
                </label>
                <div className="w-52">
                  <SelectDropdown
                    variant="panel"
                    fieldClassName="bg-raised border-line rounded-lg px-2.5 py-1.5 text-fg"
                    id="target-channel-select"
                    labelledBy="target-channel-select-label"
                    label="Target Channel"
                    options={[
                      { value: '', label: 'All Enabled Channels' },
                      ...channels.map((ch) => ({ value: String(ch.id), label: ch.name })),
                    ]}
                    value={String(presetChannelId ?? '')}
                    onChange={(v) => onSelectChannelId(v ? Number(v) : null)}
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Presets Grid */}
        {presets.length === 0 ? (
          <div className="py-8 text-center text-xs text-fg-muted">No alert presets found.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
            {visible.map((preset) => {
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
                    <span className="px-2 py-0.5 rounded-sm text-[10px] font-mono bg-warning-950/40 text-warning-300 border border-warning-800/40 shrink-0">
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
                    <span className="bg-raised px-2 py-0.5 rounded-sm text-fg-soft border border-line font-mono">
                      app: {preset.filter_app}
                    </span>
                  )}
                  {preset.match_pattern && (
                    <span className="bg-raised px-2 py-0.5 rounded-sm text-accent-400 border border-line font-mono truncate max-w-full">
                      pattern: {preset.match_pattern}
                    </span>
                  )}
                  <span className="bg-raised px-2 py-0.5 rounded-sm text-fg-soft border border-line">
                    {preset.rule_type === 'rate'
                      ? `>= ${preset.threshold_count} logs/s in ${preset.window_seconds}s`
                      : `${preset.threshold_count} in ${preset.window_seconds}s`}
                  </span>
                  <span className="bg-raised px-2 py-0.5 rounded-sm text-fg-soft border border-line">
                    cooldown: {preset.cooldown_seconds}s
                  </span>
                  {preset.ai_enrichment && (
                    <span className="bg-highlight-950/40 text-highlight-300 px-2 py-0.5 rounded-sm border border-highlight-800/40 flex items-center gap-1">
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
