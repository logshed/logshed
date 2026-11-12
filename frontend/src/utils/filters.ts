import { LogFilterParams } from '../types.ts';

/*
 * Console filters hold hosts and apps as lists only (`sources`, `apps`). Saved views stored by older
 * versions, and filters built elsewhere, may still carry the single forms (`source`, `app_name`,
 * as one name, a comma-separated string or a list): hostFilterList and appFilterList read both,
 * and withHostAppLists turns any filters into the list form at the Console's edges.
 */

function splitNames(value: string | string[] | undefined): string[] {
  if (value === undefined) return [];
  const parts = Array.isArray(value) ? value : value.split(',');
  return parts.map((s) => s.trim()).filter(Boolean);
}

/** The hosts a filter selects: `sources` when it lists any, otherwise `source`. */
export function hostFilterList(filters: LogFilterParams): string[] {
  return filters.sources && filters.sources.length > 0 ? filters.sources : splitNames(filters.source);
}

/** The apps a filter selects: `apps` when it lists any, otherwise `app_name`. */
export function appFilterList(filters: LogFilterParams): string[] {
  return filters.apps && filters.apps.length > 0 ? filters.apps : splitNames(filters.app_name);
}

/** The same filters with hosts and apps as lists only; a filter without them gets none. */
export function withHostAppLists(filters: LogFilterParams): LogFilterParams {
  const { source, app_name, ...rest } = filters;
  const out: LogFilterParams = rest;
  if (filters.sources !== undefined || source !== undefined) out.sources = hostFilterList(filters);
  if (filters.apps !== undefined || app_name !== undefined) out.apps = appFilterList(filters);
  return out;
}

/** The filters with these hosts selected, as a list (any single-form host is dropped). */
export function withHosts(filters: LogFilterParams, hosts: string[]): LogFilterParams {
  const { source: _source, ...rest } = filters;
  return { ...rest, sources: hosts };
}

/** The filters with these apps selected, as a list (any single-form app is dropped). */
export function withApps(filters: LogFilterParams, apps: string[]): LogFilterParams {
  const { app_name: _appName, ...rest } = filters;
  return { ...rest, apps };
}

/** Adds the value to the list, or takes it out when it is already there. */
export function toggleListValue(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/**
 * The filters with each selected host renamed by `replacements` (old name to new name), for hosts
 * whose alias was renamed or removed. Returns the same object when no selected host is renamed.
 */
export function renameFilterHosts(filters: LogFilterParams, replacements: Record<string, string>): LogFilterParams {
  if (!filters.sources) return filters;
  const mapped = filters.sources.map((s) => replacements[s] || s);
  if (mapped.every((s, i) => s === filters.sources![i])) return filters;
  return { ...filters, sources: mapped };
}

export function parseFiltersFromUrl(): LogFilterParams {
  if (typeof window === 'undefined') return {};
  try {
    const params = new URLSearchParams(window.location.search);
    const initial: LogFilterParams = {};

    const q = params.get('q');
    if (q) initial.query = q;

    const severity = params.get('severity');
    if (severity !== null && severity !== '') {
      const parsedSev = parseInt(severity, 10);
      if (!isNaN(parsedSev) && parsedSev >= 0 && parsedSev <= 7) {
        initial.severity_max = parsedSev;
      }
    }

    const apps = splitNames(params.get('app') ?? undefined);
    if (apps.length > 0) initial.apps = apps;

    const sources = splitNames(params.get('source') ?? undefined);
    if (sources.length > 0) initial.sources = sources;

    const time = params.get('time');
    if (time) {
      initial.from = time;
    }

    // End of an absolute range, same format as `time`; with both set the Console opens in custom-time mode
    const to = params.get('to');
    if (to) {
      initial.to = to;
    }

    return initial;
  } catch {
    return {};
  }
}

/**
 * Id of the log to scroll to and highlight, from the `focus` URL parameter (alert links set it to the
 * log that triggered the alert). It is not a filter, so it never reaches the API or a saved view.
 */
export function parseFocusLogIdFromUrl(): number | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = new URLSearchParams(window.location.search).get('focus');
    if (!raw || !/^\d+$/.test(raw)) return null;
    const id = parseInt(raw, 10);
    return id > 0 ? id : null;
  } catch {
    return null;
  }
}

export function removeFocusFromUrl() {
  try {
    const params = new URLSearchParams(window.location.search);
    if (!params.has('focus')) return;
    params.delete('focus');
    const query = params.toString();
    window.history.replaceState(null, '', query ? `${window.location.pathname}?${query}` : window.location.pathname);
  } catch {
    // Ignore URL manipulation failures in restricted environments
  }
}

export function syncFiltersToUrl(filters: LogFilterParams) {
  if (typeof window === 'undefined') return;
  try {
    const params = new URLSearchParams(window.location.search);

    if (filters.query?.trim()) {
      params.set('q', filters.query.trim());
    } else {
      params.delete('q');
    }

    if (filters.severity_max !== undefined && filters.severity_max !== null) {
      params.set('severity', String(filters.severity_max));
    } else {
      params.delete('severity');
    }

    const apps = appFilterList(filters);
    if (apps.length > 0) {
      params.set('app', apps.join(','));
    } else {
      params.delete('app');
    }

    const sources = hostFilterList(filters);
    if (sources.length > 0) {
      params.set('source', sources.join(','));
    } else {
      params.delete('source');
    }

    if (filters.from) {
      params.set('time', filters.from);
    } else {
      params.delete('time');
    }

    if (filters.to) {
      params.set('to', filters.to);
    } else {
      params.delete('to');
    }

    const newQuery = params.toString();
    const newUrl = newQuery ? `${window.location.pathname}?${newQuery}` : window.location.pathname;
    if (window.location.search !== (newQuery ? `?${newQuery}` : '')) {
      window.history.replaceState(null, '', newUrl);
    }
  } catch {
    // Ignore URL manipulation failures in restricted environments
  }
}

/** True when any Console filter (search, severity, host, app or time) is set. */
export function hasActiveLogFilters(filters: LogFilterParams): boolean {
  return Boolean(
    (filters.query && filters.query.trim()) ||
      (filters.severity_max !== undefined && filters.severity_max !== null) ||
      hostFilterList(filters).length > 0 ||
      appFilterList(filters).length > 0 ||
      filters.from ||
      filters.to
  );
}
