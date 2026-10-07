import { describe, it, expect, beforeEach } from 'vitest';
import { parseFiltersFromUrl, syncFiltersToUrl } from '../components/logs/LiveLogStream.tsx';

describe('URL Filter Deep-Linking and Synchronization', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('parses q, app, source, severity, and time from URL parameters', () => {
    window.history.replaceState(
      null,
      '',
      '/?q=connection+refused&severity=3&app=traefik,nginx&source=192.168.1.1,router&time=2026-09-01T00:00:00Z'
    );

    const filters = parseFiltersFromUrl();
    expect(filters.query).toBe('connection refused');
    expect(filters.severity_max).toBe(3);
    expect(filters.apps).toEqual(['traefik', 'nginx']);
    expect(filters.sources).toEqual(['192.168.1.1', 'router']);
    expect(filters.from).toBe('2026-09-01T00:00:00Z');
  });

  it('parses single app and single source correctly into both array and string fields', () => {
    window.history.replaceState(null, '', '/?app=traefik&source=router');

    const filters = parseFiltersFromUrl();
    expect(filters.apps).toEqual(['traefik']);
    expect(filters.app_name).toBe('traefik');
    expect(filters.sources).toEqual(['router']);
    expect(filters.source).toBe('router');
  });

  it('handles empty or malformed URL search strings gracefully', () => {
    window.history.replaceState(null, '', '/?severity=invalid&q=');

    const filters = parseFiltersFromUrl();
    expect(filters.query).toBeUndefined();
    expect(filters.severity_max).toBeUndefined();
  });

  it('synchronizes filters to browser history URL search params', () => {
    syncFiltersToUrl({
      query: 'panic',
      severity_max: 2,
      apps: ['postgres'],
      sources: ['db-01'],
      from: '2026-09-18T00:00:00Z',
    });

    const params = new URLSearchParams(window.location.search);
    expect(params.get('q')).toBe('panic');
    expect(params.get('severity')).toBe('2');
    expect(params.get('app')).toBe('postgres');
    expect(params.get('source')).toBe('db-01');
    expect(params.get('time')).toBe('2026-09-18T00:00:00Z');

    // Removing filters clears them from the URL
    syncFiltersToUrl({});
    expect(window.location.search).toBe('');
  });
});
