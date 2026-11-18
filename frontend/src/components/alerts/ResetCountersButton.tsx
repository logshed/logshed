import React, { useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { Button } from '../common/Button.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';

export interface ResetCountersButtonProps {
  /** "drop rule" or "alert rule". */
  ruleNoun: string;
  /** How many rules have a count above 0, and the sum of their counts. */
  ruleCount: number;
  total: number;
  /** What the total counts, by number: "dropped log" / "dropped logs", "firing" / "firings". */
  unit: [singular: string, plural: string];
  /** What the reset leaves as it is, said in the dialog. */
  keeps: string;
  onReset: () => Promise<void>;
}

function counted(n: number, singular: string, plural: string): string {
  return `${n.toLocaleString()} ${n === 1 ? singular : plural}`;
}

/** "Reset all counters" in a rules tab's header, with a dialog naming what it clears. */
export const ResetCountersButton: React.FC<ResetCountersButtonProps> = ({ ruleNoun, ruleCount, total, unit, keeps, onReset }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isResetting, setIsResetting] = useState(false);

  const handleConfirm = async () => {
    setIsResetting(true);
    try {
      await onReset();
    } finally {
      setIsResetting(false);
      setIsOpen(false);
    }
  };

  return (
    <>
      <Button
        onClick={() => setIsOpen(true)}
        disabled={total === 0}
        icon={<RotateCcw className="w-3.5 h-3.5" />}
        title={total === 0 ? 'Every counter is already 0' : `Set every ${ruleNoun} counter back to 0`}
      >
        Reset all counters
      </Button>
      <ConfirmDialog
        isOpen={isOpen}
        title="Reset all counters?"
        onCancel={() => setIsOpen(false)}
        onConfirm={handleConfirm}
        confirmLabel={isResetting ? 'Resetting...' : 'Reset counters'}
        confirmIcon={<RotateCcw className="w-4 h-4" />}
        confirmDisabled={isResetting}
        cancelDisabled={isResetting}
        testId="reset-counters-dialog"
      >
        <p>
          Sets the counters of {counted(ruleCount, ruleNoun, `${ruleNoun}s`)} back to 0, clearing{' '}
          {counted(total, unit[0], unit[1])}. {keeps}
        </p>
      </ConfirmDialog>
    </>
  );
};
