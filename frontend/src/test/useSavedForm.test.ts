import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useSavedForm } from '../components/settings/useSavedForm.ts';

interface Values {
  level: string;
  url: string;
  on: boolean;
}

const FALLBACK: Values = { level: 'WARNING', url: '', on: false };
const trimUrl = { url: (v: string) => v.trim() };

describe('useSavedForm', () => {
  it('starts clean on the saved values and sends nothing', () => {
    const { result } = renderHook(() => useSavedForm<Values>({ level: 'ERROR', url: 'x', on: true }, FALLBACK));
    expect(result.current.values).toEqual({ level: 'ERROR', url: 'x', on: true });
    expect(result.current.isDirty).toBe(false);
    expect(result.current.changes()).toEqual({});
  });

  it('sends only the edited fields, normalized', () => {
    const saved: Values = { level: 'WARNING', url: 'http://a', on: false };
    const { result } = renderHook(() => useSavedForm<Values>(saved, FALLBACK, trimUrl));
    act(() => result.current.set('url', '  http://b  '));
    expect(result.current.isDirty).toBe(true);
    expect(result.current.changes()).toEqual({ url: 'http://b' });
  });

  it('an edit that only adds spaces, or goes back to the saved value, is not a change', () => {
    const saved: Values = { level: 'WARNING', url: 'http://a', on: false };
    const { result } = renderHook(() => useSavedForm<Values>(saved, FALLBACK, trimUrl));
    act(() => result.current.set('url', 'http://a '));
    expect(result.current.isDirty).toBe(false);
    act(() => result.current.set('on', true));
    act(() => result.current.set('on', false));
    expect(result.current.isDirty).toBe(false);
    expect(result.current.changes()).toEqual({});
  });

  it('new saved values show in fields not edited, and an edited field keeps its edit', () => {
    let saved: Values = { level: 'WARNING', url: 'http://a', on: false };
    const { result, rerender } = renderHook(() => useSavedForm<Values>(saved, FALLBACK));
    act(() => result.current.set('level', 'ERROR'));
    // Saved elsewhere (another card, or another browser)
    saved = { level: 'WARNING', url: 'http://elsewhere', on: true };
    rerender();
    expect(result.current.values).toEqual({ level: 'ERROR', url: 'http://elsewhere', on: true });
    expect(result.current.changes()).toEqual({ level: 'ERROR' });
  });

  it('a field set back to the saved value follows later saved values', () => {
    let saved: Values = { level: 'WARNING', url: '', on: false };
    const { result, rerender } = renderHook(() => useSavedForm<Values>(saved, FALLBACK));
    act(() => result.current.set('level', 'ERROR'));
    act(() => result.current.set('level', 'WARNING'));
    saved = { ...saved, level: 'DEBUG' };
    rerender();
    expect(result.current.values.level).toBe('DEBUG');
    expect(result.current.isDirty).toBe(false);
  });

  it('is clean once the saved values match the edits (after a save)', () => {
    let saved: Values = { level: 'WARNING', url: '', on: false };
    const { result, rerender } = renderHook(() => useSavedForm<Values>(saved, FALLBACK));
    act(() => result.current.set('on', true));
    saved = { ...saved, on: true };
    rerender();
    expect(result.current.isDirty).toBe(false);
    // A later change made elsewhere is shown, not hidden by the old edit
    saved = { ...saved, on: false };
    rerender();
    expect(result.current.values.on).toBe(false);
  });

  it('reset drops every edit', () => {
    const saved: Values = { level: 'WARNING', url: '', on: false };
    const { result } = renderHook(() => useSavedForm<Values>(saved, FALLBACK));
    act(() => result.current.set('level', 'ERROR'));
    act(() => result.current.reset());
    expect(result.current.values).toEqual(saved);
    expect(result.current.isDirty).toBe(false);
  });

  it('is never dirty before the saved values have loaded', () => {
    const { result } = renderHook(() => useSavedForm<Values>(null, FALLBACK));
    act(() => result.current.set('level', 'ERROR'));
    expect(result.current.isDirty).toBe(false);
    expect(result.current.changes()).toEqual({});
  });
});
