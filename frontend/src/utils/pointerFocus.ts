/**
 * Chromium counts any key, a lone Shift included, as keyboard use for :focus-visible. So a button,
 * tab or link clicked with the mouse is outlined the moment Shift is pressed (for example to
 * Shift-click a range of Console rows next), as if the keyboard had moved focus there.
 *
 * A mouse, touch or pen press marks the page (`data-pointer-focus` on <html>), and index.css hides
 * focus outlines and focus-visible rings (not on text fields) while the mark is there. Any key but a
 * lone modifier removes it, so Tab, arrows, Enter, Space or Escape show focus again at once.
 */

export const POINTER_FOCUS_ATTR = 'data-pointer-focus';

const MODIFIER_KEYS = new Set(['Shift', 'Control', 'Alt', 'AltGraph', 'Meta']);

/**
 * Starts the listeners on window in the capture phase, before React mounts, so they run first: the
 * dropdowns stop Escape in their own window capture listeners, and stopPropagation does not stop other
 * listeners on the same target. Returns a cleanup.
 */
export function hideFocusAfterPointerModifiers(win: Window = window): () => void {
  const root = win.document.documentElement;
  const onPointerDown = () => root.setAttribute(POINTER_FOCUS_ATTR, '');
  const onKeyDown = (e: KeyboardEvent) => {
    if (!MODIFIER_KEYS.has(e.key)) root.removeAttribute(POINTER_FOCUS_ATTR);
  };
  win.addEventListener('pointerdown', onPointerDown, true);
  win.addEventListener('keydown', onKeyDown, true);
  return () => {
    win.removeEventListener('pointerdown', onPointerDown, true);
    win.removeEventListener('keydown', onKeyDown, true);
    root.removeAttribute(POINTER_FOCUS_ATTR);
  };
}
