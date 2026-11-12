import React from 'react';
import { AlertCircle, Check, RefreshCw, Save } from 'lucide-react';

export interface StickySaveBarProps {
  isDirty: boolean;
  isSavingSettings: boolean;
  /** Shown for a moment after a save. */
  saveInlineSuccess: boolean;
  /** A notice beside the "saved" message, when the save changed something else. */
  saveNotice: string | null;
  saveInlineError: string | null;
  onDiscard: () => void;
}

/**
 * The bar that floats at the bottom of the Settings form while it has unsaved edits, and shows the
 * result of a save. Its Save button submits the form it is in.
 */
export const StickySaveBar: React.FC<StickySaveBarProps> = ({
  isDirty,
  isSavingSettings,
  saveInlineSuccess,
  saveNotice,
  saveInlineError,
  onDiscard,
}) => {
  if (!(isDirty || saveInlineSuccess || saveInlineError)) return null;
  return (
    <aside
      aria-label="Unsaved changes bar"
      className={`sticky bottom-4 z-30 bg-dark-900/95 backdrop-blur-md border rounded-xl p-3 sm:px-5 sm:py-3 shadow-2xl flex flex-col sm:flex-row items-center justify-between gap-3 animate-in fade-in slide-in-from-bottom-2 ${saveInlineSuccess
          ? 'border-emerald-500/40'
          : saveInlineError
            ? 'border-red-500/40'
            : 'border-amber-500/40'
        }`}
    >
      <div className="flex items-center gap-2 text-xs font-medium">
        {saveInlineSuccess ? (
          <div className="flex items-center gap-2 text-emerald-400 font-mono">
            <Check className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>Settings saved successfully!</span>
            {saveNotice && (
              <span className="text-amber-300 font-sans">{saveNotice}</span>
            )}
          </div>
        ) : saveInlineError ? (
          <div className="flex items-center gap-2 text-red-400 font-mono">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{saveInlineError}</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-amber-300">
            <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
            <span>You have unsaved changes</span>
          </div>
        )}
      </div>

      {isDirty && (
        <div className="flex items-center gap-2 w-full sm:w-auto justify-end">
          {isSavingSettings && (
            <div className="hidden sm:flex items-center gap-1.5 text-xs text-slate-400 font-mono mr-1">
              <RefreshCw className="w-3.5 h-3.5 animate-spin text-accent-500" />
              <span>Saving...</span>
            </div>
          )}
          <button
            type="button"
            onClick={onDiscard}
            disabled={isSavingSettings}
            className="px-3 py-1.5 text-xs text-slate-300 hover:text-white bg-dark-800 hover:bg-dark-750 border border-dark-700 rounded-lg transition cursor-pointer disabled:opacity-50"
          >
            Discard
          </button>
          <button
            type="submit"
            disabled={isSavingSettings}
            className="px-4 py-1.5 text-xs font-medium text-white bg-accent-700 hover:bg-accent-600 rounded-lg transition cursor-pointer flex items-center justify-center gap-1.5 shadow-md disabled:opacity-50"
          >
            {isSavingSettings ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            <span>Save Changes</span>
          </button>
        </div>
      )}
    </aside>
  );
};
