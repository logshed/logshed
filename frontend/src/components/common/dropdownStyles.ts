/**
 * The look shared by the Console's filter dropdowns (MultiSelectDropdown's filter variant and
 * SelectDropdown's toolbar variant): a bordered box that is the trigger button, an accent border while
 * it filters, and a clear button laid over the box as the trigger's sibling (never inside it).
 */
export function filterTriggerClass(isOpen: boolean, active: boolean): string {
  const state = isOpen
    ? 'border-accent-500 ring-1 ring-accent-500/20'
    : active
    ? 'border-accent-600/60 bg-dark-900/90 text-slate-100'
    : 'border-dark-700 hover:border-slate-600 text-slate-300';
  return `flex items-center gap-1.5 bg-dark-900 border rounded px-2.5 py-1 text-xs cursor-pointer select-none transition focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20 ${state}`;
}

/** The clear button over a filter trigger: its right edge is the trigger's padding plus the chevron and gap. */
export const FILTER_CLEAR_BUTTON =
  'absolute right-7 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-200 p-0.5 rounded hover:bg-dark-800 transition';

/** Room kept inside the trigger, before the chevron, for the clear button laid over it. */
export const FILTER_CLEAR_ROOM = 'w-[18px] shrink-0';

/** The open list of a dropdown. */
export const POPOVER_PANEL =
  'bg-dark-900 border border-dark-700 rounded-md shadow-2xl py-1.5';
