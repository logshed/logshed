import { useEffect, useRef, type RefObject } from 'react';

/**
 * Open dialogs, bottom first. One keydown listener serves them all: Esc closes only the top
 * dialog, and Tab and Shift+Tab stay inside it. Dropdowns inside a dialog that handle Esc
 * themselves stop the event in the capture phase or call preventDefault, so it never gets here.
 */
interface DialogEntry {
  containerRef: RefObject<HTMLElement | null>;
  onCloseRef: RefObject<() => void>;
  closeOnEscapeRef: RefObject<boolean>;
  /** Where focus goes back on close: the opener first, then the openers of the dialogs it was in. */
  openersRef: RefObject<HTMLElement[]>;
}

const stack: DialogEntry[] = [];

const FOCUSABLE = [
  'a[href]',
  'area[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'iframe',
  '[contenteditable]:not([contenteditable="false"])',
  '[tabindex]',
].join(',');

/** Keyboard-focusable elements inside container, in Tab order (positive tabindex is not used). */
export function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.tabIndex >= 0 && !el.closest('[inert]') && !el.hasAttribute('hidden'),
  );
}

/** Number of dialogs open now (for components outside dialogs that also listen for Esc). */
export function openDialogCount(): number {
  return stack.length;
}

function handleKeyDown(e: KeyboardEvent): void {
  const top = stack[stack.length - 1];
  if (!top) return;
  if (e.key === 'Escape') {
    if (e.defaultPrevented) return;
    e.preventDefault();
    if (top.closeOnEscapeRef.current) top.onCloseRef.current();
    return;
  }
  if (e.key !== 'Tab') return;
  const container = top.containerRef.current;
  if (!container) return;
  const items = focusableElements(container);
  if (items.length === 0) {
    e.preventDefault();
    container.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  const inside = active instanceof Node && container.contains(active);
  if (e.shiftKey && (!inside || active === first || active === container)) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (!inside || active === last)) {
    e.preventDefault();
    first.focus();
  }
}

/**
 * The element that has focus now, followed by the openers of the open dialog it sits in (and so
 * on down the stack). When a dialog replaces the one it was opened from, as Create Drop Rule
 * replaces the log drawer, its own opener is gone on close and focus goes to the drawer's opener.
 */
function currentOpeners(): HTMLElement[] {
  const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  if (!active || active === document.body) return [];
  for (let i = stack.length - 1; i >= 0; i--) {
    if (stack[i].containerRef.current?.contains(active)) return [active, ...(stack[i].openersRef.current ?? [])];
  }
  return [active];
}

function push(entry: DialogEntry): void {
  if (stack.length === 0) window.addEventListener('keydown', handleKeyDown);
  stack.push(entry);
}

function remove(entry: DialogEntry): void {
  const index = stack.indexOf(entry);
  if (index >= 0) stack.splice(index, 1);
  if (stack.length === 0) window.removeEventListener('keydown', handleKeyDown);
}

export interface UseDialogOptions {
  /** Esc closes the dialog (default true). When false, Esc does nothing while it is on top. */
  closeOnEscape?: boolean;
}

/**
 * Makes the element in containerRef a modal dialog while isOpen: it joins the dialog stack, gets
 * focus (unless something inside already took it, such as an autoFocus field; otherwise the
 * first element marked data-autofocus, else the container itself, which needs tabIndex={-1}),
 * keeps Tab inside, closes on Esc when on top, and gives focus back on close.
 */
export function useDialog(
  isOpen: boolean,
  onClose: () => void,
  containerRef: RefObject<HTMLElement | null>,
  { closeOnEscape = true }: UseDialogOptions = {},
): void {
  // Read through refs, so a new onClose on every parent render does not reopen the dialog
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const closeOnEscapeRef = useRef(closeOnEscape);
  closeOnEscapeRef.current = closeOnEscape;

  // Read during the render that opens the dialog: by the time effects run, an autoFocus field
  // inside the dialog has already taken focus and the opener is no longer known
  const openersRef = useRef<HTMLElement[]>([]);
  const wasOpenRef = useRef(false);
  if (isOpen && !wasOpenRef.current) openersRef.current = currentOpeners();
  wasOpenRef.current = isOpen;

  useEffect(() => {
    if (!isOpen) return;
    const entry: DialogEntry = { containerRef, onCloseRef, closeOnEscapeRef, openersRef };
    push(entry);

    const container = containerRef.current;
    if (container && !(document.activeElement && container.contains(document.activeElement))) {
      const preferred = container.querySelector<HTMLElement>('[data-autofocus]');
      (preferred ?? container).focus();
    }

    return () => {
      remove(entry);
      // The dialog's elements are gone (or going): return focus to what opened it, if it is still
      // on the page and nothing else (such as the dialog below) took focus meanwhile
      const active = document.activeElement;
      const focusLost = !active || active === document.body || (container !== null && container.contains(active));
      if (!focusLost) return;
      const target = openersRef.current.find((el) => el.isConnected && !(container?.contains(el) ?? false));
      if (target) target.focus();
      else stack[stack.length - 1]?.containerRef.current?.focus();
    };
    // containerRef is a stable ref object; onClose and closeOnEscape are read through refs
  }, [isOpen]);
}
