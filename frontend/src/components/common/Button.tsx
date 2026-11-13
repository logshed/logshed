import React from 'react';

/**
 * The app's buttons. Each variant has one look everywhere; `className` is for layout only (width,
 * margins, flex placement), never colour, padding, font or corners.
 *
 * - primary: the main action of a card or dialog (white on accent-700, 5.9:1).
 * - secondary: other actions, and Cancel.
 * - danger: an action that deletes or revokes something.
 * - danger-soft: a red-tinted action that throws work away but is not the main one (Discard Changes).
 * - soft: an accent-tinted action that sits beside content rather than ending a form (install a preset,
 *   save the current view).
 * - ghost: a quiet action in a toolbar or row, with no fill until hovered.
 *
 * Every variant has a 1 px border (transparent where it shows none), so buttons of different variants
 * side by side are the same height.
 */
export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'danger-soft' | 'soft' | 'ghost';
export type ButtonSize = 'sm' | 'md' | 'lg';

const BASE =
  'inline-flex items-center justify-center gap-1.5 rounded-sm border font-medium whitespace-nowrap transition cursor-pointer select-none disabled:opacity-50 disabled:cursor-not-allowed';

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'border-transparent bg-accent-700 hover:bg-accent-600 text-on-fill shadow-xs',
  secondary: 'border-line bg-raised hover:bg-raised-hover text-fg-soft hover:text-fg-max',
  danger: 'border-transparent bg-danger-600 hover:bg-danger-500 text-on-fill shadow-xs',
  'danger-soft': 'border-danger-800/60 bg-danger-950/40 hover:bg-danger-900/40 text-danger-400 hover:text-danger-300',
  soft: 'border-accent-500/30 bg-accent-500/10 hover:bg-accent-500/20 text-accent-400',
  ghost: 'border-transparent text-fg-muted hover:text-fg hover:bg-raised',
};

// Below md, a small button is at least 32 px tall, as icon buttons are, so it is easy to tap
const SIZES: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1 text-[11px] max-md:min-h-8',
  md: 'px-3.5 py-1.5 text-xs',
  lg: 'px-4 py-2.5 text-sm',
};

/** The classes of a button's look, for the few links and labels that must look like a button. */
export function buttonClass(variant: ButtonVariant = 'secondary', size: ButtonSize = 'md'): string {
  return `${BASE} ${VARIANTS[variant]} ${SIZES[size]}`;
}

export interface ButtonProps extends React.ComponentPropsWithRef<'button'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** An icon before the label. */
  icon?: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'secondary',
  size = 'md',
  icon,
  type = 'button',
  className,
  children,
  ...rest
}) => (
  <button
    type={type}
    className={className ? `${buttonClass(variant, size)} ${className}` : buttonClass(variant, size)}
    {...rest}
  >
    {icon}
    {children}
  </button>
);

export type IconButtonVariant = 'ghost' | 'secondary' | 'danger';

const ICON_VARIANTS: Record<IconButtonVariant, string> = {
  ghost: 'border-transparent text-fg-muted hover:text-fg hover:bg-raised',
  secondary: 'border-line bg-raised hover:bg-raised-hover text-fg-muted hover:text-fg-max',
  // A quiet delete or remove: grey until hovered
  danger: 'border-transparent text-fg-muted hover:text-danger-400 hover:bg-raised',
};

// Below md (where the tables become cards), every icon button is at least 32 px square, so it is easy to tap
const ICON_SIZES: Record<ButtonSize, string> = {
  sm: 'p-1 max-md:min-w-8 max-md:min-h-8',
  md: 'p-1.5 max-md:min-w-8 max-md:min-h-8',
  lg: 'p-2',
};

export interface IconButtonProps extends Omit<React.ComponentPropsWithRef<'button'>, 'aria-label'> {
  /** The button's name for screen readers, and its tooltip unless `title` is given. */
  label: string;
  variant?: IconButtonVariant;
  size?: ButtonSize;
  children: React.ReactNode;
}

/** A button that shows only an icon. It always has a name. */
export const IconButton: React.FC<IconButtonProps> = ({
  label,
  variant = 'ghost',
  size = 'md',
  type = 'button',
  title,
  className,
  children,
  ...rest
}) => {
  const look = `inline-flex items-center justify-center shrink-0 rounded-sm border transition cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${ICON_VARIANTS[variant]} ${ICON_SIZES[size]}`;
  return (
    <button
      type={type}
      aria-label={label}
      title={title ?? label}
      className={className ? `${look} ${className}` : look}
      {...rest}
    >
      {children}
    </button>
  );
};
