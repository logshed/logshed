import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { IncidentHistoryDetail } from '../components/alerts/IncidentHistoryDetail.tsx';
import { AlertHistoryItem } from '../types.ts';

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
    expect(screen.getByText('(5,000 logs analyzed)')).toBeInTheDocument();
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
