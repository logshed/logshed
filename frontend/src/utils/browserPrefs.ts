/**
 * Settings remembered by this browser only (Console column widths, the histogram's on/off), in
 * localStorage under versioned keys. Storage can be missing, full or blocked (a private window,
 * cleared site data), so every read and write is guarded: a value that cannot be read, or fails
 * its check, counts as not set, and the caller falls back to its default.
 */

const PREFIX = 'logshed.';

/** The stored value of key, checked by parse (which returns null for anything it does not accept). */
export function readBrowserPref<T>(key: string, parse: (value: unknown) => T | null): T | null {
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    if (raw === null) return null;
    return parse(JSON.parse(raw));
  } catch {
    return null;
  }
}

/** Stores value under key; quietly does nothing when storage is unavailable. */
export function writeBrowserPref(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // Not remembered: the setting still applies until the page is reloaded
  }
}

/** Forgets key; quietly does nothing when storage is unavailable. */
export function removeBrowserPref(key: string): void {
  try {
    window.localStorage.removeItem(PREFIX + key);
  } catch {
    // Nothing stored that could be read either
  }
}
