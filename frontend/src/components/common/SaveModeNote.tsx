import React from 'react';
import { Save, Zap } from 'lucide-react';

export type SaveMode = 'bar' | 'instant';

const TEXT: Record<SaveMode, string> = {
  bar: 'Saved with Save Changes',
  instant: 'Each change saves at once',
};

/**
 * Says how a settings card saves, under its description (`CardHeader`'s `note`): its fields wait
 * for the Save Changes bar, or each change (adding, editing or deleting an item) saves at once.
 */
export const SaveModeNote: React.FC<{ mode: SaveMode }> = ({ mode }) => {
  const Icon = mode === 'bar' ? Save : Zap;
  return (
    <p className="flex items-center gap-1.5 text-[11px] text-fg-muted" data-save-mode={mode}>
      <Icon className="w-3 h-3 shrink-0" aria-hidden="true" />
      <span>{TEXT[mode]}</span>
    </p>
  );
};
