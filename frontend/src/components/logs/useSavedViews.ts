import { useCallback, useEffect, useMemo, useState } from 'react';
import { LogFilterParams, SavedView } from '../../types.ts';
import { fetchSavedViews, createSavedView, updateSavedView, deleteSavedView } from '../../api/savedViews.ts';

/** Pinned views first, then by name. */
export function sortSavedViews(views: SavedView[]): SavedView[] {
  return [...views].sort(
    (a, b) => (b.is_pinned ? 1 : 0) - (a.is_pinned ? 1 : 0) || a.name.localeCompare(b.name)
  );
}

/** The filter fields a saved view stores: only the ones that are set. */
export function savedViewParamsFromFilters(filters: LogFilterParams): Record<string, any> {
  const params: Record<string, any> = {};
  if (filters.query?.trim()) params.query = filters.query.trim();
  if (filters.severity_max !== undefined && filters.severity_max !== null) {
    params.severity_max = filters.severity_max;
  }
  if (filters.sources && filters.sources.length > 0) {
    params.sources = filters.sources;
  } else if (filters.source) {
    params.source = filters.source;
  }
  if (filters.apps && filters.apps.length > 0) {
    params.apps = filters.apps;
  } else if (filters.app_name) {
    params.app_name = filters.app_name;
  }
  if (filters.from) params.from = filters.from;
  if (filters.to) params.to = filters.to;
  return params;
}

const toList = (value: unknown): string[] => {
  const items = Array.isArray(value) ? value : typeof value === 'string' ? value.split(',') : [];
  return Array.from(new Set(items.map((v) => String(v).trim()).filter(Boolean))).sort();
};

// Paging fields and the host and app fields (handled together with their aliases) are skipped
// by the generic pass in canonicalViewParams.
const SPECIAL_KEYS = new Set(['limit', 'offset', 'source', 'sources', 'app_name', 'apps']);

/**
 * Filters in a comparable form: hosts and apps as one sorted list each (`sources` or `source`,
 * `apps` or `app_name`), other lists sorted, strings trimmed, and empty strings, empty lists and
 * missing values all left out.
 */
export function canonicalViewParams(params: Record<string, any>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const hosts = toList(params.sources && params.sources.length > 0 ? params.sources : params.source);
  const apps = toList(params.apps && params.apps.length > 0 ? params.apps : params.app_name);
  if (hosts.length > 0) out.sources = hosts;
  if (apps.length > 0) out.apps = apps;

  for (const [key, value] of Object.entries(params)) {
    if (SPECIAL_KEYS.has(key) || value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      const list = toList(value);
      if (list.length > 0) out[key] = list;
    } else if (typeof value === 'string') {
      const trimmed = value.trim();
      if (!trimmed) continue;
      out[key] = key === 'severity_max' && Number.isFinite(Number(trimmed)) ? Number(trimmed) : trimmed;
    } else {
      out[key] = value;
    }
  }
  return out;
}

const stableKey = (params: Record<string, unknown>): string =>
  JSON.stringify(Object.keys(params).sort().map((key) => [key, params[key]]));

/** The first saved view whose stored filters equal the current filters, or null. */
export function findActiveView(views: SavedView[], filters: LogFilterParams): SavedView | null {
  if (views.length === 0) return null;
  const current = stableKey(canonicalViewParams(filters as Record<string, any>));
  return views.find((view) => stableKey(canonicalViewParams(view.query_params || {})) === current) ?? null;
}

/** True when another saved view already uses this name, ignoring case and outer spaces. */
export function isSavedViewNameTaken(views: SavedView[], name: string, ignoreId?: number): boolean {
  const wanted = name.trim().toLowerCase();
  if (!wanted) return false;
  return views.some((view) => view.id !== ignoreId && view.name.trim().toLowerCase() === wanted);
}

export interface SavedViewsState {
  views: SavedView[];
  isLoading: boolean;
  /** Last pin or delete error, shown next to the Views menu. */
  error: string | null;
  /** Saves the current filters as a new view; throws on failure (the Save View modal shows it). */
  createView: (name: string, filters: LogFilterParams, isPinned: boolean) => Promise<SavedView>;
  togglePin: (view: SavedView) => Promise<void>;
  /** Renames a view; throws on failure (the rename modal shows it). */
  renameView: (viewId: number, name: string) => Promise<SavedView>;
  deleteView: (viewId: number) => Promise<void>;
}

/**
 * The saved views list shared by the search bar (Views menu and mobile drawer) and the Quick
 * Filters bar (pinned chips). Pass `enabled = false` to skip loading when the list comes from
 * elsewhere.
 */
export function useSavedViews(enabled: boolean = true): SavedViewsState {
  const [views, setViews] = useState<SavedView[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setIsLoading(true);
    fetchSavedViews()
      .then((data) => {
        if (!cancelled) setViews(sortSavedViews(data));
      })
      .catch(() => {
        // Non-critical background fetch
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  const replaceView = useCallback((updated: SavedView) => {
    setViews((prev) => sortSavedViews(prev.map((v) => (v.id === updated.id ? updated : v))));
  }, []);

  const createView = useCallback(async (name: string, filters: LogFilterParams, isPinned: boolean) => {
    const created = await createSavedView({
      name,
      query_params: savedViewParamsFromFilters(filters),
      is_pinned: isPinned,
    });
    setViews((prev) => sortSavedViews([...prev, created]));
    return created;
  }, []);

  const togglePin = useCallback(async (view: SavedView) => {
    try {
      setError(null);
      replaceView(await updateSavedView(view.id, { is_pinned: !view.is_pinned }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update saved view');
    }
  }, [replaceView]);

  const renameView = useCallback(async (viewId: number, name: string) => {
    const updated = await updateSavedView(viewId, { name });
    replaceView(updated);
    return updated;
  }, [replaceView]);

  const deleteView = useCallback(async (viewId: number) => {
    try {
      setError(null);
      await deleteSavedView(viewId);
      setViews((prev) => prev.filter((v) => v.id !== viewId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete saved view');
    }
  }, []);

  return useMemo(
    () => ({ views, isLoading, error, createView, togglePin, renameView, deleteView }),
    [views, isLoading, error, createView, togglePin, renameView, deleteView]
  );
}
