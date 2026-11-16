import React from 'react';
import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import { Button } from '../common/Button.tsx';

export interface Feedback {
  text: string;
  isError: boolean;
}

/** The result of an action in a settings card, until the next action or Dismiss. */
export const FeedbackBanner: React.FC<{ feedback: Feedback | null; onDismiss: () => void }> = ({ feedback, onDismiss }) => {
  if (!feedback) return null;
  return (
    <div
      className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${
        feedback.isError
          ? 'bg-danger-950/60 border-danger-800 text-danger-300'
          : 'bg-success-950/60 border-success-800 text-success-300'
      }`}
    >
      {feedback.isError ? (
        <AlertTriangle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
      ) : (
        <CheckCircle2 className="w-4 h-4 text-success-400 shrink-0 mt-0.5" />
      )}
      <span className="flex-1">{feedback.text}</span>
      <Button variant="ghost" size="sm" onClick={onDismiss} className="-my-1">
        Dismiss
      </Button>
    </div>
  );
};
