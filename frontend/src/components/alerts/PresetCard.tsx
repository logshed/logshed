import React from 'react';
import { Plus, RefreshCw, CheckCircle2 } from 'lucide-react';

export interface PresetCardProps {
  name: string;
  isCustom?: boolean;
  /** Shown at the top right, for example the rule type. */
  badge: React.ReactNode;
  description: string;
  /** The chips that describe what the preset matches. */
  children: React.ReactNode;
  alreadyInstalled: boolean;
  installedText: string;
  notInstalledText: string;
  isInstalling: boolean;
  onInstall: () => void;
}

/** One preset in an alert or drop preset catalog, with its install button. */
export const PresetCard: React.FC<PresetCardProps> = ({
  name,
  isCustom,
  badge,
  description,
  children,
  alreadyInstalled,
  installedText,
  notInstalledText,
  isInstalling,
  onInstall,
}) => (
  <div className="bg-canvas/70 border border-line-subtle hover:border-line-hover rounded-xl p-4 flex flex-col justify-between space-y-3 transition">
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-fg-strong truncate">{name}</span>
          {isCustom && (
            <span className="px-1.5 py-0.5 rounded-sm text-[10px] font-medium bg-info-950/60 text-info-300 border border-info-800 shrink-0">
              Custom
            </span>
          )}
        </div>
        {badge}
      </div>

      <p className="text-xs text-fg-muted leading-relaxed">{description}</p>

      <div className="pt-1 flex flex-wrap gap-1.5 text-[11px] text-fg-muted">{children}</div>
    </div>

    <div className="pt-3 border-t border-line-subtle flex items-center justify-between gap-2">
      <span className="text-[11px] text-fg-muted">
        {alreadyInstalled ? (
          <span className="flex items-center gap-1 text-success-400">
            <CheckCircle2 className="w-3 h-3 shrink-0" />
            <span>{installedText}</span>
          </span>
        ) : (
          notInstalledText
        )}
      </span>
      <button
        type="button"
        onClick={onInstall}
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
