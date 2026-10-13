import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { IncidentHistoryDetail } from '../components/alerts/IncidentHistoryDetail.tsx';
import { AlertHistoryTab } from '../components/alerts/tabs/AlertHistoryTab.tsx';
import {
  isMaintenanceItem,
  maintenanceEndLabel,
  maintenanceSummary,
} from '../utils/maintenanceHistory.ts';
import { AlertHistoryItem } from '../types.ts';

const baseItem: AlertHistoryItem = {
  id: 7,
  rule_id: null,
  rule_name: 'Maintenance Window',
  trigger_count: 0,
  ai_enrichment: false,
  triggered_at: '2026-10-13T10:00:00Z',
  maintenance_session_id: 'sess-1',
  maintenance_type: 'drop_errors',
  maintenance_started_at: '2026-10-13T10:00:00Z',
  maintenance_ended_at: null,
  maintenance_end_reason: null,
  maintenance_trigger: 'Deploy Bot',
  maintenance_target_app: 'batch',
  maintenance_target_host: null,
  maintenance_dropped_count: 12,
};

describe('maintenance history helpers', () => {
  it('detects maintenance rows by session id', () => {
    expect(isMaintenanceItem(baseItem)).toBe(true);
    expect(isMaintenanceItem({ ...baseItem, maintenance_session_id: null })).toBe(false);
  });

  it('labels in progress, expired and early termination', () => {
    expect(maintenanceEndLabel(baseItem)).toBe('In progress');
    expect(
      maintenanceEndLabel({ ...baseItem, maintenance_ended_at: '2026-10-13T11:00:00Z', maintenance_end_reason: 'expired' })
    ).toBe('Expired');
    expect(
      maintenanceEndLabel({ ...baseItem, maintenance_ended_at: '2026-10-13T10:30:00Z', maintenance_end_reason: 'terminated' })
    ).toBe('Ended early');
  });

  it('summarizes scope and dropped count', () => {
    expect(maintenanceSummary(baseItem)).toBe('Drop errors - app batch - 12 logs dropped - in progress');
    expect(
      maintenanceSummary({
        ...baseItem,
        maintenance_type: 'silence_alerts',
        maintenance_target_app: null,
        maintenance_ended_at: '2026-10-13T11:00:00Z',
        maintenance_end_reason: 'expired',
      })
    ).toBe('Silence alerts - all apps and hosts - expired');
  });
});

describe('IncidentHistoryDetail maintenance variant', () => {
  it('shows trigger, type, targets and dropped count for an in-progress window', () => {
    render(<IncidentHistoryDetail item={baseItem} />);
    expect(screen.getByTestId('maintenance-detail')).toBeInTheDocument();
    expect(screen.getByText('Maintenance Window')).toBeInTheDocument();
    expect(screen.getAllByText('In progress').length).toBeGreaterThan(0);
    expect(screen.getByText('Deploy Bot')).toBeInTheDocument();
    expect(screen.getByText('Drop errors')).toBeInTheDocument();
    expect(screen.getByText('batch')).toBeInTheDocument();
    expect(screen.getByText('All hosts')).toBeInTheDocument();
    expect(screen.getByText('Logs dropped')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
  });

  it('labels early termination and hides drop count for silence mode', () => {
    render(
      <IncidentHistoryDetail
        item={{
          ...baseItem,
          maintenance_type: 'silence_alerts',
          maintenance_ended_at: '2026-10-13T10:30:00Z',
          maintenance_end_reason: 'terminated',
          maintenance_dropped_count: 0,
        }}
      />
    );
    expect(screen.getByText('Ended early')).toBeInTheDocument();
    expect(screen.getByText('Ended early at')).toBeInTheDocument();
    expect(screen.queryByText('Logs dropped')).not.toBeInTheDocument();
    expect(screen.getByText(/Only alert notifications were silenced/)).toBeInTheDocument();
  });
});

describe('AlertHistoryTab maintenance rows', () => {
  it('renders a Maintenance badge and summary instead of an alert row', () => {
    render(
      <AlertHistoryTab
        historyItems={[baseItem]}
        historyTotal={1}
        isMobile={false}
        onClearAllHistory={vi.fn()}
        onSelectHistoryItem={vi.fn()}
        onDeleteHistoryItem={vi.fn()}
      />
    );
    expect(screen.getByText('Maintenance')).toBeInTheDocument();
    expect(screen.getByText('Maintenance Window')).toBeInTheDocument();
    expect(screen.getByText(/12 logs dropped/)).toBeInTheDocument();
    expect(screen.queryByText('Alert')).not.toBeInTheDocument();
  });
});
