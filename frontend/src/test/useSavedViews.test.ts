import { describe, it, expect } from 'vitest';
import {
  canonicalViewParams,
  findActiveView,
  isSavedViewNameTaken,
  savedViewParamsFromFilters,
  sortSavedViews,
} from '../components/logs/useSavedViews.ts';
import { SavedView } from '../types.ts';

const view = (id: number, name: string, query_params: SavedView['query_params'], is_pinned = false): SavedView => ({
  id,
  name,
  query_params,
  is_pinned,
  created_at: '2026-10-01T00:00:00Z',
});

describe('saved view helpers (WP5-1, WP5-4)', () => {
  it('treats empty strings, empty lists and missing values alike and sorts lists', () => {
    expect(canonicalViewParams({ query: '', sources: [], apps: undefined, from: null })).toEqual({});
    expect(canonicalViewParams({ sources: ['b', 'a'], source: 'b,a' })).toEqual({ sources: ['a', 'b'] });
    expect(canonicalViewParams({ source: 'b,a' })).toEqual(canonicalViewParams({ sources: ['a', 'b'] }));
    expect(canonicalViewParams({ app_name: ['y', 'x'] })).toEqual({ apps: ['x', 'y'] });
    expect(canonicalViewParams({ query: ' disk ', limit: 500, offset: 0 })).toEqual({ query: 'disk' });
  });

  it('finds the view whose filters equal the current filters', () => {
    const views = [
      view(1, 'Errors', { severity_max: 3, query: 'disk' }),
      view(2, 'Hosts', { sources: ['pve2', 'pve1'] }),
      view(3, 'Everything', {}),
    ];
    expect(findActiveView(views, { query: 'disk', severity_max: 3, sources: [], app_name: undefined })?.id).toBe(1);
    expect(findActiveView(views, { sources: ['pve1', 'pve2'], source: 'pve1,pve2' })?.id).toBe(2);
    expect(findActiveView(views, { query: '' })?.id).toBe(3);
    expect(findActiveView(views, { query: 'disk' })).toBeNull();
    expect(findActiveView(views, { query: 'disk', severity_max: 3, from: '1h' })).toBeNull();
    expect(findActiveView([], {})).toBeNull();
  });

  it('checks names ignoring case and outer spaces, skipping the view being renamed', () => {
    const views = [view(1, 'Errors', {}), view(2, 'Router', {})];
    expect(isSavedViewNameTaken(views, ' errors ')).toBe(true);
    expect(isSavedViewNameTaken(views, 'ERRORS', 1)).toBe(false);
    expect(isSavedViewNameTaken(views, 'Firewall')).toBe(false);
    expect(isSavedViewNameTaken(views, '  ')).toBe(false);
  });

  it('saves only the filters that are set, and sorts pinned views first', () => {
    expect(savedViewParamsFromFilters({ query: ' disk ', sources: [], source: 'pve1', apps: ['nginx'], from: '1h' })).toEqual({
      query: 'disk',
      source: 'pve1',
      apps: ['nginx'],
      from: '1h',
    });
    const sorted = sortSavedViews([view(1, 'b', {}), view(2, 'z', {}, true), view(3, 'a', {})]);
    expect(sorted.map((v) => v.id)).toEqual([2, 3, 1]);
  });
});
