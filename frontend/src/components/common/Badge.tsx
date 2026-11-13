import React from 'react';

export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

const TONES: Record<BadgeTone, string> = {
  neutral: 'bg-raised text-fg-soft border-line',
  accent: 'bg-accent-950/40 text-accent-400 border-accent-800/60',
  success: 'bg-success-950/40 text-success-400 border-success-800/60',
  warning: 'bg-warning-950/40 text-warning-300 border-warning-800/60',
  danger: 'bg-danger-950/40 text-danger-400 border-danger-800/60',
};

export function badgeClass(tone: BadgeTone = 'neutral'): string {
  return `inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm border text-[10px] font-medium whitespace-nowrap ${TONES[tone]}`;
}

export interface BadgeProps extends React.ComponentPropsWithRef<'span'> {
  tone?: BadgeTone;
  /** Monospace upper case, for codes such as a scope or a protocol. */
  code?: boolean;
}

/** A small label that states something (a status, a count, a kind); it is not a control. */
export const Badge: React.FC<BadgeProps> = ({ tone = 'neutral', code = false, className, children, ...rest }) => {
  const look = `${badgeClass(tone)}${code ? ' font-mono uppercase' : ''}`;
  return (
    <span className={className ? `${look} ${className}` : look} {...rest}>
      {children}
    </span>
  );
};
