import React, { useState } from 'react';
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react';
import { AiModelInfo } from '../../types.ts';
import { aiModelOption, getOrdinalSuffix } from '../../utils/aiPrompt.ts';
import { SelectDropdown } from '../common/SelectDropdown.tsx';

// Settings has room for a larger list; the AI analysis dialog uses a compact one
const SIZES = {
  roomy: {
    wrapper: 'space-y-2',
    empty: 'p-3 bg-canvas border border-line-subtle rounded-lg text-[11px] text-fg-muted font-mono',
    list: 'space-y-1.5 max-h-48 overflow-y-auto',
    row: 'p-2',
    rowButton: 'p-1',
    addArea: 'space-y-1.5 pt-1',
    field: 'px-2.5 py-1.5',
    addButton: 'px-3 py-1.5',
    cancelButton: 'p-1.5',
  },
  compact: {
    wrapper: 'space-y-1.5',
    empty: 'p-2 bg-canvas border border-line-subtle rounded-sm text-[11px] text-fg-muted font-mono',
    list: 'space-y-1 max-h-36 overflow-y-auto',
    row: 'p-1.5',
    rowButton: 'p-0.5',
    addArea: 'pt-1',
    field: 'px-2 py-1',
    addButton: 'px-2.5 py-1',
    cancelButton: 'p-1',
  },
} as const;

export interface ModelChainEditorProps {
  /** The fallback models in the order they are tried. */
  fallbacks: string[];
  /** The primary model, left out of the models offered as fallbacks. */
  primaryModel: string;
  availableModels: AiModelInfo[];
  onChange: (fallbacks: string[]) => void;
  /** Moving the first fallback up: the parent swaps it with the primary model. */
  onPromoteFirst: () => void;
  heading: string;
  /** A short note beside the heading. */
  headingNote?: string;
  emptyText: string;
  size: keyof typeof SIZES;
}

/**
 * The ordered list of fallback models, with buttons to reorder and remove them and a picker to add
 * one (from the provider's models or typed). Remount it (a new key) to clear the picker.
 */
export const ModelChainEditor: React.FC<ModelChainEditorProps> = ({
  fallbacks,
  primaryModel,
  availableModels,
  onChange,
  onPromoteFirst,
  heading,
  headingNote,
  emptyText,
  size,
}) => {
  const [selectedToAdd, setSelectedToAdd] = useState<string>('');
  const [customInput, setCustomInput] = useState<string>('');
  const [showCustomInput, setShowCustomInput] = useState<boolean>(false);
  const s = SIZES[size];

  const move = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= fallbacks.length) return;
    const copy = [...fallbacks];
    const [removed] = copy.splice(index, 1);
    copy.splice(target, 0, removed);
    onChange(copy);
  };

  const remove = (index: number) => {
    onChange(fallbacks.filter((_, i) => i !== index));
  };

  const add = (modelName: string) => {
    const trimmed = modelName.trim();
    if (!trimmed || fallbacks.includes(trimmed)) return;
    onChange([...fallbacks, trimmed]);
  };

  const label = <label className="block text-[11px] font-semibold text-fg-muted uppercase">{heading}</label>;

  return (
    <div className={s.wrapper}>
      {headingNote ? (
        <div className="flex items-center justify-between">
          {label}
          <span className="text-[11px] text-fg-muted font-mono">{headingNote}</span>
        </div>
      ) : (
        label
      )}

      {fallbacks.length === 0 ? (
        <div className={s.empty}>{emptyText}</div>
      ) : (
        <div className={s.list}>
          {fallbacks.map((fb, idx) => {
            const isThinking = availableModels.find((m) => m.id === fb)?.supports_thinking;
            return (
              <div
                key={idx}
                className={`flex items-center justify-between ${s.row} bg-canvas border border-line-subtle rounded-sm text-xs font-mono`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="px-1.5 py-0.5 rounded-sm bg-warning-500/20 text-warning-300 border border-warning-500/30 text-[10px] font-semibold shrink-0">
                    {`${getOrdinalSuffix(idx + 1)} Fallback`}
                  </span>
                  <span className="text-fg font-medium truncate">{fb}</span>
                  {isThinking && (
                    <span className="px-1 py-0.2 rounded-sm bg-marker-500/20 text-marker-300 border border-marker-500/30 text-[9px] shrink-0">
                      Reasoning
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0 ml-2">
                  <button
                    type="button"
                    onClick={() => (idx === 0 ? onPromoteFirst() : move(idx, -1))}
                    className={`${s.rowButton} text-fg-muted hover:text-fg cursor-pointer`}
                    title={idx === 0 ? 'Swap with primary model' : 'Move up in priority'}
                    aria-label={idx === 0 ? 'Make primary model' : 'Move fallback up'}
                  >
                    <ArrowUp className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    disabled={idx === fallbacks.length - 1}
                    onClick={() => move(idx, 1)}
                    className={`${s.rowButton} text-fg-muted hover:text-fg disabled:opacity-20 disabled:cursor-not-allowed cursor-pointer`}
                    title="Move down in priority"
                    aria-label="Move fallback down"
                  >
                    <ArrowDown className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(idx)}
                    className={`${s.rowButton} text-danger-400 hover:text-danger-300 cursor-pointer`}
                    title="Remove fallback model"
                    aria-label="Remove fallback"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className={s.addArea}>
        {!showCustomInput ? (
          <div className="flex items-center gap-2 w-full">
            <div className="flex-1 min-w-0">
              <SelectDropdown
                variant="panel"
                fieldClassName={`bg-canvas border-line rounded-sm ${s.field} font-mono text-fg`}
                label="Add Fallback Model"
                options={[
                  { value: '', label: '-- Add Fallback Model --' },
                  ...availableModels.filter((m) => m.id !== primaryModel && !fallbacks.includes(m.id)).map(aiModelOption),
                  { value: '__custom_fallback__', label: 'Custom fallback model...' },
                ]}
                value={selectedToAdd}
                onChange={(v) => {
                  if (v === '__custom_fallback__') {
                    setShowCustomInput(true);
                    setSelectedToAdd('');
                  } else {
                    setSelectedToAdd(v);
                  }
                }}
              />
            </div>
            <button
              type="button"
              disabled={!selectedToAdd}
              onClick={() => {
                add(selectedToAdd);
                setSelectedToAdd('');
              }}
              className={`shrink-0 ${s.addButton} bg-raised hover:bg-raised-hover border border-line rounded-sm text-xs text-fg font-mono flex items-center gap-1 disabled:opacity-40 cursor-pointer`}
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add</span>
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2 w-full">
            <input
              type="text"
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              placeholder="Enter custom model ID"
              className={`flex-1 min-w-0 bg-canvas border border-line rounded-sm ${s.field} text-xs text-fg focus:outline-hidden focus:border-accent-500 font-mono`}
            />
            <button
              type="button"
              disabled={!customInput.trim()}
              onClick={() => {
                add(customInput);
                setCustomInput('');
                setShowCustomInput(false);
              }}
              className={`shrink-0 ${s.addButton} bg-raised hover:bg-raised-hover border border-line rounded-sm text-xs text-fg font-mono flex items-center gap-1 disabled:opacity-40 cursor-pointer`}
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setShowCustomInput(false);
                setCustomInput('');
              }}
              className={`shrink-0 ${s.cancelButton} text-fg-muted hover:text-fg cursor-pointer`}
              title="Cancel custom fallback"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
