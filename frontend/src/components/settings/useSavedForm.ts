import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/** How a field is compared with its saved value and sent: for example, a text field trimmed. */
export type FieldNormalizers<T> = { [K in keyof T]?: (value: T[K]) => T[K] };

export interface SavedForm<T> {
  /** The saved values with the user's edits on top. */
  values: T;
  set: <K extends keyof T>(key: K, value: T[K]) => void;
  /** True when some field differs from its saved value. */
  isDirty: boolean;
  /** Only the fields that differ from their saved values, normalized, ready to send. */
  changes: () => Partial<T>;
  /** Drops every edit, back to the saved values. */
  reset: () => void;
}

/**
 * A form over saved settings that sends only what the user changed. The edits are kept apart from
 * the saved values, so new saved values (after this form's save, or a save elsewhere on the page)
 * show at once in every field the user has not edited, and a field the user edited keeps the edit
 * until it is saved or discarded. An edit that matches the saved value again is dropped.
 */
export function useSavedForm<T extends object>(
  saved: T | null,
  fallback: T,
  normalizers: FieldNormalizers<T> = {},
): SavedForm<T> {
  const [edits, setEdits] = useState<Partial<T>>({});
  const base = saved ?? fallback;

  const same = useCallback(
    <K extends keyof T>(key: K, a: T[K], b: T[K]) => {
      const norm = normalizers[key];
      return norm ? Object.is(norm(a), norm(b)) : Object.is(a, b);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the normalizers are fixed per form; callers pass a new object literal each render
    [],
  );

  // New saved values: an edit that now matches them was saved (here or elsewhere) and is dropped.
  useEffect(() => {
    if (!saved) return;
    setEdits((prev) => {
      const keys = Object.keys(prev) as (keyof T)[];
      const kept = keys.filter((k) => !same(k, prev[k] as T[typeof k], saved[k]));
      if (kept.length === keys.length) return prev;
      const next: Partial<T> = {};
      for (const k of kept) next[k] = prev[k];
      return next;
    });
  }, [saved, same]);

  const values = useMemo(() => ({ ...base, ...edits }) as T, [base, edits]);

  const changedKeys = (Object.keys(edits) as (keyof T)[]).filter(
    (k) => !same(k, edits[k] as T[typeof k], base[k]),
  );
  const isDirty = saved !== null && changedKeys.length > 0;

  // Read inside set, so set keeps one identity while the saved values change
  const baseRef = useRef(base);
  baseRef.current = base;

  const set = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setEdits((prev) => {
      if (same(key, value, baseRef.current[key])) {
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return { ...prev, [key]: value };
    });
  }, [same]);

  const changes = () => {
    const out: Partial<T> = {};
    if (!saved) return out;
    for (const k of changedKeys) {
      const norm = normalizers[k];
      const v = edits[k] as T[typeof k];
      out[k] = norm ? norm(v) : v;
    }
    return out;
  };

  const reset = useCallback(() => setEdits({}), []);

  return { values, set, isDirty, changes, reset };
}
