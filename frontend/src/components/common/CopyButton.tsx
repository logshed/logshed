import React from 'react';
import { Check, Copy } from 'lucide-react';
import { useClipboard } from '../../utils/hooks.ts';
import { buttonClass } from './Button.tsx';

// ghost: the small grey button beside a block of text; link: accent text, beside a command to copy;
// button: a secondary Button, beside a read-only field; compact: in a code block's header bar;
// icon: the icon alone, with the label for screen readers and the title on hover (a value in a list)
const LOOKS = {
  button: buttonClass('secondary'),
  // compact: the small grey button in the header bar of a code block
  compact:
    'inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm text-[11px] text-fg-muted hover:text-fg hover:bg-raised transition cursor-pointer select-none',
  ghost:
    'inline-flex items-center gap-1.5 px-2 py-1 rounded-sm text-[11px] font-medium text-fg-muted hover:text-fg hover:bg-raised active:bg-raised-hover transition cursor-pointer min-h-[32px] touch-manipulation select-none',
  icon: 'inline-flex items-center justify-center w-6 h-6 max-md:w-8 max-md:h-8 shrink-0 rounded-sm text-fg-muted hover:text-fg hover:bg-raised active:bg-raised-hover transition cursor-pointer touch-manipulation select-none',
  link: 'inline-flex items-center gap-1 text-[11px] font-medium text-accent-400 hover:text-accent-300 active:text-accent-200 cursor-pointer min-h-[32px] touch-manipulation select-none',
} as const;

export interface CopyButtonProps {
  /** The text to copy, or a function that returns it at click time. Nothing is copied when it is empty. */
  text: string | (() => string);
  /** Label shown until the copy succeeds; "Copied" replaces it for timeoutMs. */
  label?: string;
  title?: string;
  variant?: keyof typeof LOOKS;
  /** Layout classes only (placement, margins). */
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
  variant = 'ghost',
  className,
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
    <button
      type="button"
      onClick={handleClick}
      className={className ? `${LOOKS[variant]} ${className}` : LOOKS[variant]}
      title={title ?? (variant === 'icon' ? label : undefined)}
    >
      {copied ? <Check className={`${iconClassName} text-success-400`} /> : <Copy className={iconClassName} />}
      {variant === 'icon' ? (
        <span className="sr-only">{copied ? 'Copied' : label}</span>
      ) : copied ? (
        <span className={copiedLabelClassName}>Copied</span>
      ) : (
        <span>{label}</span>
      )}
    </button>
  );
};
