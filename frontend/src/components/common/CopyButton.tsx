import React from 'react';
import { Check, Copy } from 'lucide-react';
import { useClipboard } from '../../utils/hooks.ts';

// The small ghost button most copy actions use
const DEFAULT_CLASS =
  'inline-flex items-center gap-1.5 px-2 py-1 rounded-sm text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition cursor-pointer min-h-[32px] touch-manipulation select-none active:bg-dark-750';

export interface CopyButtonProps {
  /** The text to copy, or a function that returns it at click time. Nothing is copied when it is empty. */
  text: string | (() => string);
  /** Label shown until the copy succeeds; "Copied" replaces it for timeoutMs. */
  label?: string;
  title?: string;
  /** The button's classes; each caller keeps its current look until the buttons share one style. */
  className?: string;
  iconClassName?: string;
  /** Extra classes on the "Copied" label. */
  copiedLabelClassName?: string;
  timeoutMs?: number;
  /** Runs before the copy, for example to select the text in a field. */
  beforeCopy?: () => void;
}

export const CopyButton: React.FC<CopyButtonProps> = ({
  text,
  label = 'Copy',
  title,
  className = DEFAULT_CLASS,
  iconClassName = 'w-3.5 h-3.5',
  copiedLabelClassName,
  timeoutMs,
  beforeCopy,
}) => {
  const { copied, copy } = useClipboard(timeoutMs);

  const handleClick = async () => {
    const value = typeof text === 'function' ? text() : text;
    if (!value) return;
    beforeCopy?.();
    await copy(value);
  };

  return (
    <button type="button" onClick={handleClick} className={className} title={title}>
      {copied ? <Check className={`${iconClassName} text-emerald-400`} /> : <Copy className={iconClassName} />}
      {copied ? <span className={copiedLabelClassName}>Copied</span> : <span>{label}</span>}
    </button>
  );
};
