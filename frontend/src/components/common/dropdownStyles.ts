/**
 * The look shared by the Console's filter dropdowns (MultiSelectDropdown's filter variant and
 * SelectDropdown's toolbar variant): a bordered box that is the trigger button, an accent border while
 * it filters, and a clear button laid over the box as the trigger's sibling (never inside it).
 */
export function filterTriggerClass(isOpen: boolean, active: boolean): string {
  const state = isOpen
    ? 'border-accent-500 ring-1 ring-accent-500/20'
    : active
    ? 'border-accent-600/60 bg-surface/90 text-fg-strong'
    : 'border-line hover:border-line-stronger text-fg-soft';
  return `flex items-center gap-1.5 bg-surface border rounded-sm px-2.5 py-1 text-xs cursor-pointer select-none transition focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20 ${state}`;
}

/** The clear button over a filter trigger: its right edge is the trigger's padding plus the chevron and gap. */
export const FILTER_CLEAR_BUTTON =
  'absolute right-7 top-1/2 -translate-y-1/2 text-fg-subtle hover:text-fg p-0.5 rounded-sm hover:bg-raised transition';

/** Room kept inside the trigger, before the chevron, for the clear button laid over it. */
export const FILTER_CLEAR_ROOM = 'w-[18px] shrink-0';

/** The open list of a dropdown. */
export const POPOVER_PANEL =
  'bg-surface border border-line rounded-md shadow-2xl py-1.5';
