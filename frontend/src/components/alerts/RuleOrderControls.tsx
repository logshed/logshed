import React from 'react';
import { ChevronDown, ChevronUp, GripVertical } from 'lucide-react';

export interface RuleOrderControlsProps {
  ruleId: number;
  index: number;
  count: number;
  onMove: (index: number, direction: 'up' | 'down') => void;
}

const MOVE_BUTTON_CLASS =
  'w-8 h-8 sm:w-auto sm:h-auto sm:p-0.5 flex items-center justify-center rounded-md sm:rounded text-slate-500 hover:text-slate-200 active:bg-dark-800 disabled:opacity-20 disabled:hover:text-slate-500 transition cursor-pointer disabled:cursor-not-allowed';

/** The move up and down buttons around the drag handle at the start of a rule row. */
export const RuleOrderControls: React.FC<RuleOrderControlsProps> = ({ ruleId, index, count, onMove }) => (
  <div className="flex flex-col items-center gap-2 sm:gap-0.5 shrink-0 pt-0.5 text-slate-500">
    <button
      type="button"
      onClick={() => onMove(index, 'up')}
      disabled={index === 0}
      aria-label={`Move rule ${ruleId} up`}
      title="Move up"
      className={MOVE_BUTTON_CLASS}
    >
      <ChevronUp className="w-4 h-4 sm:w-3.5 sm:h-3.5" />
    </button>
    <div
      className="hidden sm:flex cursor-grab active:cursor-grabbing p-0.5 text-slate-500 hover:text-slate-300"
      title="Drag to reorder"
    >
      <GripVertical className="w-4 h-4" />
    </div>
    <button
      type="button"
      onClick={() => onMove(index, 'down')}
      disabled={index === count - 1}
      aria-label={`Move rule ${ruleId} down`}
      title="Move down"
      className={MOVE_BUTTON_CLASS}
    >
      <ChevronDown className="w-4 h-4 sm:w-3.5 sm:h-3.5" />
    </button>
  </div>
);
