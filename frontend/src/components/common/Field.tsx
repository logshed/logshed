import React, { useId } from 'react';

/** A field's label: plain text above the control. LABEL_TEXT_CLASS is the same without the space under it,
    for a label that shares a row with links or a button. */
export const LABEL_TEXT_CLASS = 'block text-xs font-medium text-fg-soft';
export const LABEL_CLASS = `${LABEL_TEXT_CLASS} mb-1`;
const HINT_CLASS = 'text-[11px] text-fg-muted mt-1';
const ERROR_CLASS = 'text-[11px] text-danger-400 mt-1';

/**
 * The look of a text box. Touch screens get 16 px text from index.css whatever the size here, so iOS
 * Safari does not zoom in on focus. The placeholder colour comes from the base layer.
 */
const CONTROL_BASE =
  'w-full bg-canvas border rounded-sm px-3 py-2 text-xs text-fg focus:outline-hidden focus:border-accent-500 disabled:opacity-50 disabled:cursor-not-allowed';

export function controlClass({ invalid = false, mono = false }: { invalid?: boolean; mono?: boolean } = {}): string {
  return `${CONTROL_BASE} ${invalid ? 'border-danger-600' : 'border-line'}${mono ? ' font-mono' : ''}`;
}

/** SelectDropdown's `fieldClassName` in a form, so a dropdown matches the text boxes beside it. */
export const SELECT_FIELD_CLASS = 'bg-canvas border-line rounded-sm px-3 py-2 text-fg';

/** What a Field gives its control, so the label, hint and error are announced with it. */
export interface FieldControlProps {
  id: string;
  /** The label's id, for controls named with aria-labelledby (SelectDropdown's `labelledBy`). */
  labelId: string;
  'aria-describedby'?: string;
  'aria-invalid'?: true;
}

export interface FieldProps {
  label: React.ReactNode;
  /** Help under the control. */
  hint?: React.ReactNode;
  /** An error under the control, in place of the hint; marks the control invalid. */
  error?: React.ReactNode;
  /** The control's id; one is made when it is not given. */
  id?: string;
  /** Layout classes of the wrapper. */
  className?: string;
  children: (control: FieldControlProps) => React.ReactNode;
}

/** A label, a control and its hint or error, with the ids wired for screen readers. */
export const Field: React.FC<FieldProps> = ({ label, hint, error, id, className, children }) => {
  const madeId = useId();
  const controlId = id ?? madeId;
  const labelId = `${controlId}-label`;
  const noteId = `${controlId}-note`;
  const note = error ?? hint;
  return (
    <div className={className}>
      <label id={labelId} htmlFor={controlId} className={LABEL_CLASS}>
        {label}
      </label>
      {children({
        id: controlId,
        labelId,
        'aria-describedby': note ? noteId : undefined,
        'aria-invalid': error ? true : undefined,
      })}
      {note && (
        <p id={noteId} className={error ? ERROR_CLASS : HINT_CLASS}>
          {note}
        </p>
      )}
    </div>
  );
};

interface LabelledProps {
  /** A visible label above the box; without it, name the box with aria-label. */
  label?: React.ReactNode;
  hint?: React.ReactNode;
  error?: React.ReactNode;
  /** Monospace text, for addresses, patterns and URLs. */
  mono?: boolean;
  /** Layout classes of the wrapper (with a label) or of the box itself (without). */
  className?: string;
}

export type TextFieldProps = LabelledProps & Omit<React.ComponentPropsWithRef<'input'>, 'className'>;

/** A one-line text box, with an optional label, hint and error. */
export const TextField: React.FC<TextFieldProps> = ({ label, hint, error, mono, className, id, ...rest }) => {
  const look = controlClass({ invalid: Boolean(error), mono });
  if (label === undefined) {
    return <input id={id} className={className ? `${look} ${className}` : look} {...rest} />;
  }
  return (
    <Field label={label} hint={hint} error={error} id={id} className={className}>
      {({ labelId: _labelId, ...control }) => <input className={look} {...control} {...rest} />}
    </Field>
  );
};

export type TextAreaProps = LabelledProps & Omit<React.ComponentPropsWithRef<'textarea'>, 'className'>;

/** A text box of several lines, with an optional label, hint and error. */
export const TextArea: React.FC<TextAreaProps> = ({ label, hint, error, mono, className, id, ...rest }) => {
  const look = `${controlClass({ invalid: Boolean(error), mono })} resize-y`;
  if (label === undefined) {
    return <textarea id={id} className={className ? `${look} ${className}` : look} {...rest} />;
  }
  return (
    <Field label={label} hint={hint} error={error} id={id} className={className}>
      {({ labelId: _labelId, ...control }) => <textarea className={look} {...control} {...rest} />}
    </Field>
  );
};

/** The look of a checkbox: the browser's own control, ticked in the accent colour. */
export const CHECKBOX_CLASS =
  'w-4 h-4 shrink-0 accent-accent-600 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed';

export interface CheckboxProps extends Omit<React.ComponentPropsWithRef<'input'>, 'type' | 'className'> {
  /** Text beside the box; the whole line then toggles it. Without it, name the box with aria-label. */
  label?: React.ReactNode;
  /** Layout classes of the label line (with a label) or of the box (without). */
  className?: string;
}

export const Checkbox: React.FC<CheckboxProps> = ({ label, className, ...rest }) => {
  if (label === undefined) {
    return <input type="checkbox" className={className ? `${CHECKBOX_CLASS} ${className}` : CHECKBOX_CLASS} {...rest} />;
  }
  const line = 'inline-flex items-center gap-2 text-xs text-fg-soft cursor-pointer select-none';
  return (
    <label className={className ? `${line} ${className}` : line}>
      <input type="checkbox" className={CHECKBOX_CLASS} {...rest} />
      <span>{label}</span>
    </label>
  );
};
