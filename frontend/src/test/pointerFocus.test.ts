import { describe, it, expect, afterEach } from 'vitest';
import { fireEvent } from '@testing-library/react';
import { hideFocusAfterPointerModifiers, POINTER_FOCUS_ATTR } from '../utils/pointerFocus.ts';

describe('hideFocusAfterPointerModifiers', () => {
  let stop: (() => void) | null = null;
  afterEach(() => stop?.());
  const marked = () => document.documentElement.hasAttribute(POINTER_FOCUS_ATTR);

  it('marks the page on a pointer press and keeps the mark through lone modifier keys', () => {
    stop = hideFocusAfterPointerModifiers();
    const button = document.body.appendChild(document.createElement('button'));
    expect(marked()).toBe(false);
    fireEvent.pointerDown(button);
    expect(marked()).toBe(true);
    for (const key of ['Shift', 'Control', 'Alt', 'AltGraph', 'Meta']) fireEvent.keyDown(button, { key });
    expect(marked()).toBe(true);
    button.remove();
  });

  it('drops the mark on any other key, even Escape stopped by a window capture listener added later', () => {
    stop = hideFocusAfterPointerModifiers();
    const button = document.body.appendChild(document.createElement('button'));
    // As SelectDropdown, MultiSelectDropdown and SavedViewsMenu do with Escape while open
    const stopper = (e: KeyboardEvent) => e.stopPropagation();
    window.addEventListener('keydown', stopper, true);
    fireEvent.pointerDown(button);
    fireEvent.keyDown(button, { key: 'Escape' });
    expect(marked()).toBe(false);
    fireEvent.pointerDown(button);
    fireEvent.keyDown(button, { key: 'Tab', shiftKey: true });
    expect(marked()).toBe(false);
    window.removeEventListener('keydown', stopper, true);
    button.remove();
  });

  it('removes its listeners and the mark on cleanup', () => {
    stop = hideFocusAfterPointerModifiers();
    fireEvent.pointerDown(document.body);
    stop();
    stop = null;
    expect(marked()).toBe(false);
    fireEvent.pointerDown(document.body);
    expect(marked()).toBe(false);
  });
});
