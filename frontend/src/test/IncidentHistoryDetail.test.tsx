import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { IncidentHistoryDetail } from '../components/alerts/IncidentHistoryDetail.tsx';
import { AlertHistoryItem } from '../types.ts';
import { parseFiltersFromUrl } from '../components/logs/LiveLogStream.tsx';
import * as settingsApi from '../api/settings.ts';

describe('IncidentHistoryDetail Component', () => {
  it('renders daily digest and hides link to LogShed from history view', () => {
    const digestItem: AlertHistoryItem = {
      id: 1,
      rule_id: null,
      rule_name: 'Daily Digest',
      channel_id: 10,
      trigger_count: 5000,
      sample_log: 'Top error entity: api-service (42 errors)',
      incident_summary: `### 24-Hour Analytical Rollup
- **Total Logs Ingested:** 5,000
- **System Storage Delta:** +1.2 MB

### Top Apps / Hosts with Errors or Above
- **api-service**: 42 events

### Top Logging Services
- **frontend**: 1,000 logs

[Link to LogShed (digest filters applied)](http://localhost:8000/?time=2026-09-30T08%3A18%3A32.480Z&severity=3)`,
      ai_enrichment: false,
      ai_model: null,
      ai_audit_id: null,
      triggered_at: '2026-10-06T14:38:53Z',
    };

    render(<IncidentHistoryDetail item={digestItem} channelName="Homelab Discord" />);

    // Daily digest title and metrics are rendered
    expect(screen.getByText('Daily Digest Rollup Report')).toBeInTheDocument();
    expect(screen.getByText('(5,000 matching logs)')).toBeInTheDocument();
    expect(screen.getByText('Homelab Discord')).toBeInTheDocument();

    // The analytical content is rendered
    expect(screen.getByText(/24-Hour Analytical Rollup/i)).toBeInTheDocument();
    expect(screen.getByText(/api-service/i)).toBeInTheDocument();

    // The link to LogShed must be hidden in the history detail view
    expect(screen.queryByText(/Link to LogShed \(digest filters applied\)/i)).toBeNull();
    expect(screen.queryByRole('link', { name: /Link to LogShed/i })).toBeNull();
  });

  it('renders standard alert rule summary without modifying markdown links', () => {
    const alertItem: AlertHistoryItem = {
      id: 2,
      rule_id: 5,
      rule_name: 'Database Connection Failures',
      channel_id: 10,
      trigger_count: 3,
      sample_log: 'FATAL: database connection lost',
      incident_summary: 'See documentation at [Internal Wiki](http://wiki.local/troubleshoot) for assistance.',
      ai_enrichment: false,
      ai_model: null,
      ai_audit_id: null,
      triggered_at: '2026-10-06T14:38:53Z',
    };

    render(<IncidentHistoryDetail item={alertItem} channelName="Homelab Discord" />);

    expect(screen.getByText(/See documentation at/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Internal Wiki/i })).toHaveAttribute(
      'href',
      'http://wiki.local/troubleshoot'
    );
  });
});

describe('IncidentHistoryDetail "Inspect in Console" (WP6-1)', () => {
  const ruleAlert: AlertHistoryItem = {
    id: 3,
    rule_id: 7,
    rule_name: 'Disk errors',
    channel_id: null,
    trigger_count: 4,
    sample_log: 'I/O error on /dev/sda1',
    incident_summary: null,
    ai_enrichment: false,
    ai_model: null,
    ai_audit_id: null,
    triggered_at: '2026-10-13T10:00:00Z',
    source_alias: 'nas-01',
    app_name: 'kernel',
  };

  const paramsOf = (el: HTMLElement) => new URL(el.getAttribute('href') || '', 'http://localhost').searchParams;

  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState(null, '', '/');
  });

  it('opens the Console from 5 minutes before to 5 minutes after the alert when the rule is not loaded', () => {
    render(<IncidentHistoryDetail item={ruleAlert} />);
    const link = screen.getByRole('link', { name: 'Inspect in Console' });
    const params = paramsOf(link);
    expect(new URL(link.getAttribute('href') || '', 'http://localhost').pathname).toBe('/');
    expect(params.get('source')).toBe('nas-01');
    expect(params.get('time')).toBe('2026-10-13T09:55:00.000Z');
    expect(params.get('to')).toBe('2026-10-13T10:05:00.000Z');
    // Host-wide by default
    expect(params.has('app')).toBe(false);
  });

  it('starts at the rule window when it is longer than 5 minutes, and never less than 5 minutes', () => {
    const { rerender } = render(<IncidentHistoryDetail item={ruleAlert} ruleWindowSeconds={3600} />);
    expect(paramsOf(screen.getByRole('link', { name: 'Inspect in Console' })).get('time')).toBe('2026-10-13T09:00:00.000Z');
    rerender(<IncidentHistoryDetail item={ruleAlert} ruleWindowSeconds={60} />);
    expect(paramsOf(screen.getByRole('link', { name: 'Inspect in Console' })).get('time')).toBe('2026-10-13T09:55:00.000Z');
  });

  it('offers "Same app only", which adds the app filter', () => {
    render(<IncidentHistoryDetail item={ruleAlert} />);
    const params = paramsOf(screen.getByRole('link', { name: 'Same app only' }));
    expect(params.get('app')).toBe('kernel');
    expect(params.get('source')).toBe('nas-01');
    expect(params.get('time')).toBe('2026-10-13T09:55:00.000Z');
  });

  it('leaves out source and "Same app only" when the item has no source or app', () => {
    render(<IncidentHistoryDetail item={{ ...ruleAlert, source_alias: null, app_name: null }} />);
    const params = paramsOf(screen.getByRole('link', { name: 'Inspect in Console' }));
    expect(params.has('source')).toBe(false);
    expect(screen.queryByRole('link', { name: 'Same app only' })).toBeNull();
  });

  it('shows a smaller link next to the triggering log snippet', () => {
    render(<IncidentHistoryDetail item={ruleAlert} />);
    const snippetLink = screen.getByRole('link', { name: 'View in Console' });
    expect(snippetLink.getAttribute('href')).toBe(
      screen.getByRole('link', { name: 'Inspect in Console' }).getAttribute('href')
    );
  });

  it('switches to the Console with an SPA navigation on a plain click', () => {
    const popstate = vi.fn();
    window.addEventListener('popstate', popstate);
    render(<IncidentHistoryDetail item={ruleAlert} />);
    fireEvent.click(screen.getByRole('link', { name: 'Same app only' }));
    window.removeEventListener('popstate', popstate);
    expect(popstate).toHaveBeenCalled();
    expect(window.location.pathname).toBe('/');
    const filters = parseFiltersFromUrl();
    expect(filters.source).toBe('nas-01');
    expect(filters.app_name).toBe('kernel');
    expect(filters.from).toBe('2026-10-13T09:55:00.000Z');
    expect(filters.to).toBe('2026-10-13T10:05:00.000Z');
  });

  it('uses UTC times that parseFiltersFromUrl reads, whatever the browser and server time zones', () => {
    const originalTz = process.env.TZ;
    process.env.TZ = 'America/New_York';
    vi.spyOn(settingsApi, 'getCachedServerTimezone').mockReturnValue('Pacific/Kiritimati');
    try {
      // Stored without a zone suffix: the backend writes UTC
      render(<IncidentHistoryDetail item={{ ...ruleAlert, triggered_at: '2026-10-13 23:30:00' }} />);
      fireEvent.click(screen.getByRole('link', { name: 'Inspect in Console' }));
      const filters = parseFiltersFromUrl();
      expect(filters.from).toBe('2026-10-13T23:25:00.000Z');
      expect(filters.to).toBe('2026-10-13T23:35:00.000Z');
    } finally {
      process.env.TZ = originalTz;
    }
  });

  it('hides the Console links for Daily Digest, On-Demand and maintenance items', () => {
    const { rerender } = render(
      <IncidentHistoryDetail item={{ ...ruleAlert, rule_id: null, rule_name: 'Daily Digest' }} />
    );
    expect(screen.queryByRole('link', { name: 'Inspect in Console' })).toBeNull();
    rerender(
      <IncidentHistoryDetail
        item={{ ...ruleAlert, rule_id: null, rule_name: 'On-Demand Analysis', sample_log: null, ai_audit_id: 4 }}
      />
    );
    expect(screen.queryByRole('link', { name: 'Inspect in Console' })).toBeNull();
    rerender(
      <IncidentHistoryDetail
        item={{ ...ruleAlert, rule_id: null, rule_name: 'Maintenance Window', maintenance_session_id: 's1', maintenance_started_at: '2026-10-13T10:00:00Z' } as AlertHistoryItem}
      />
    );
    expect(screen.queryByRole('link', { name: 'Inspect in Console' })).toBeNull();
  });

  it('hides the Console links when the trigger time cannot be read', () => {
    render(<IncidentHistoryDetail item={{ ...ruleAlert, triggered_at: '' }} />);
    expect(screen.queryByRole('link', { name: 'Inspect in Console' })).toBeNull();
  });
});
