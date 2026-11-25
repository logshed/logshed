import React from 'react';

/**
 * A card: the raised box each section of a page sits in. `padded` holds a form or text, `flush` holds a
 * list or table that runs to its edges. `className` is for layout and spacing between children only.
 */
const CARD_BASE = 'bg-surface border border-line rounded-xl shadow-md';
const CARD_PADDING = {
  padded: 'p-3.5 sm:p-5 space-y-4',
  flush: 'overflow-hidden',
} as const;

export function cardClass(padding: keyof typeof CARD_PADDING = 'padded'): string {
  return `${CARD_BASE} ${CARD_PADDING[padding]}`;
}

export interface CardProps extends React.ComponentPropsWithRef<'section'> {
  padding?: keyof typeof CARD_PADDING;
}

export const Card: React.FC<CardProps> = ({ padding = 'padded', className, children, ...rest }) => (
  <section className={className ? `${cardClass(padding)} ${className}` : cardClass(padding)} {...rest}>
    {children}
  </section>
);

/** A section heading: small upper-case text, with an accent icon before it. */
export const SECTION_TITLE_CLASS = 'text-xs font-semibold text-fg-soft uppercase tracking-wider flex items-center gap-2';

export interface CardHeaderProps {
  title: React.ReactNode;
  /** An icon element, sized and coloured here. */
  icon?: React.ReactElement<{ className?: string }>;
  description?: React.ReactNode;
  /** Buttons or a status at the right (under the title on a phone). */
  actions?: React.ReactNode;
  /** The heading level; h3 inside a page section. */
  as?: 'h2' | 'h3' | 'h4';
  className?: string;
}

export const CardHeader: React.FC<CardHeaderProps> = ({ title, icon, description, actions, as: Heading = 'h3', className }) => {
  const text = (
    <div className="min-w-0">
      <Heading className={SECTION_TITLE_CLASS}>
        {icon && React.cloneElement(icon, { className: 'w-4 h-4 text-accent-500 shrink-0' })}
        <span>{title}</span>
      </Heading>
      {description && <p className="text-xs text-fg-muted mt-0.5">{description}</p>}
    </div>
  );
  if (!actions) return className ? <div className={className}>{text}</div> : text;
  const row = 'flex flex-col sm:flex-row sm:items-center justify-between gap-2';
  return (
    <div className={className ? `${row} ${className}` : row}>
      {text}
      <div className="flex flex-wrap items-center gap-2 shrink-0 self-start sm:self-auto">{actions}</div>
    </div>
  );
};
