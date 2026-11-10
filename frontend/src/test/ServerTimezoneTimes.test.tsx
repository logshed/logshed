import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { IncidentHistoryDetail } from '../components/alerts/IncidentHistoryDetail.tsx';
import { AlertRulesTab } from '../components/alerts/tabs/AlertRulesTab.tsx';
import { PrunePreviewModal } from '../components/storage/PrunePreviewModal.tsx';
import { NotificationsCard } from '../components/settings/NotificationsCard.tsx';
import { getMaintenanceBannerText } from '../components/common/Navbar.tsx';
import { formatDate, formatMaintenanceTime } from '../utils/formatters.ts';
import * as settingsApi from '../api/settings.ts';
import * as notifApi from '../api/notifications.ts';
import { AlertHistoryItem, AlertRule, MaintenanceWindowResponse, PrunePreview } from '../types.ts';

// UTC+14 all year, so 10:00 UTC is midnight of the next day there, whatever the runner's zone
const SERVER_TZ = 'Pacific/Kiritimati';

const mockServerTimezone = () =>
  vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ server_timezone: SERVER_TZ } as any);

describe('times shown in the server time zone', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('incident detail shows the trigger time in the server time zone', async () => {
    mockServerTimezone();
    const item: AlertHistoryItem = {
      id: 1,
      rule_id: 3,
      rule_name: 'Disk errors',
      trigger_count: 2,
      ai_enrichment: false,
      triggered_at: '2026-10-13T10:00:00Z',
    };
    render(<IncidentHistoryDetail item={item} />);
    expect(await screen.findByText('2026-10-14 00:00:00')).toBeInTheDocument();
  });

  it('maintenance detail shows start and end in the server time zone', async () => {
    mockServerTimezone();
    const item: AlertHistoryItem = {
      id: 2,
      rule_id: null,
      rule_name: 'Maintenance Window',
      trigger_count: 0,
      ai_enrichment: false,
      triggered_at: '2026-10-13T10:00:00Z',
      maintenance_session_id: 'sess-1',
      maintenance_type: 'silence',
      maintenance_started_at: '2026-10-13T10:00:00Z',
      maintenance_ended_at: '2026-10-13T11:00:00Z',
      maintenance_end_reason: 'expired',
    };
    render(<IncidentHistoryDetail item={item} />);
    expect(await screen.findByText('2026-10-14 00:00:00')).toBeInTheDocument();
    expect(screen.getByText('2026-10-14 01:00:00')).toBeInTheDocument();
  });

  it('alert rules list shows the last triggered time in the server time zone', async () => {
    mockServerTimezone();
    const rule: AlertRule = {
      id: 1,
      name: 'Disk errors',
      rule_type: 'pattern',
      threshold_count: 1,
      window_seconds: 60,
      cooldown_seconds: 300,
      ai_enrichment: false,
      is_enabled: true,
      trigger_count: 4,
      last_triggered_at: '2026-10-13T10:00:00Z',
      created_at: '2026-10-01T00:00:00Z',
    };
    render(
      <AlertRulesTab
        rules={[rule]}
        channels={[]}
        isMaintenanceActiveNow={false}
        maintenance={{ active: false, until: null }}
        onClearMaintenance={vi.fn()}
        onNavigateToMaintenance={vi.fn()}
        onOpenPresetsModal={vi.fn()}
        onExportAll={vi.fn()}
        onFileImport={vi.fn()}
        fileInputRef={{ current: null }}
        onOpenCreateModal={vi.fn()}
        onToggleRule={vi.fn()}
        onOpenTestModal={vi.fn()}
        onExportSingle={vi.fn()}
        onOpenEditModal={vi.fn()}
        onDeleteRule={vi.fn()}
        onNavigateToSettings={vi.fn()}
        getChannelStatus={() => ({ name: 'None', warning: null, isInvalid: false })}
      />
    );
    expect(await screen.findByText('Last triggered: 2026-10-14 00:00:00')).toBeInTheDocument();
  });

  it('prune preview shows the cut-off in the server time zone', async () => {
    mockServerTimezone();
    const preview: PrunePreview = {
      retention_days: 7,
      cutoff: '2026-10-13T10:00:00Z',
      logs: 10,
      history_alerts: 0,
      history_on_demand: 0,
      history_digests: 0,
      history_maintenance: 0,
      history_total: 0,
      metrics: 0,
      ai_audit: 0,
      total: 10,
    };
    render(
      <PrunePreviewModal
        isOpen
        mode="purge"
        preview={preview}
        days={7}
        busy={false}
        onDeleteNow={vi.fn()}
        onDeleteLater={vi.fn()}
        onCancel={vi.fn()}
      />
    );
    expect(await screen.findByText(/2026-10-14 00:00:00/)).toBeInTheDocument();
  });

  it('digest card shows the last run in the server time zone', async () => {
    vi.spyOn(notifApi, 'fetchNotificationChannels').mockResolvedValue([]);
    render(
      <NotificationsCard
        settings={{ server_timezone: SERVER_TZ, daily_digest_last_run: '2026-10-13T10:00:00Z' } as any}
        digestEnabled={false}
        onDigestEnabledChange={vi.fn()}
        digestChannelId={null}
        onDigestChannelIdChange={vi.fn()}
        digestScheduleTime="09:00"
        onDigestScheduleTimeChange={vi.fn()}
      />
    );
    expect(await screen.findByText('2026-10-14 00:00:00')).toBeInTheDocument();
  });

  it('maintenance end time uses the server day and clock', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    // 23:00 on 13 Oct at UTC+14, so 11:00 UTC is already the next day there
    vi.setSystemTime(new Date('2026-10-13T09:00:00Z'));
    const text = formatMaintenanceTime('2026-10-13T11:00:00Z', SERVER_TZ);
    expect(text).toMatch(/Oct 14/);
    expect(text).toMatch(/01:00/);

    const banner = getMaintenanceBannerText(
      { active: true, until: '2026-10-13T11:00:00Z' } as MaintenanceWindowResponse,
      SERVER_TZ
    );
    expect(banner).toMatch(/until Oct 14 01:00/);
  });

  it('maintenance end time shows only the clock on the same server day', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-13T10:30:00Z'));
    expect(formatMaintenanceTime('2026-10-13T11:00:00Z', SERVER_TZ)).not.toMatch(/Oct/);
  });

  it('falls back to browser time for an unknown time zone', () => {
    expect(formatMaintenanceTime('2026-10-13T11:00:00Z', 'Not/AZone')).toBe(
      formatMaintenanceTime('2026-10-13T11:00:00Z')
    );
    expect(formatDate('2026-10-13T11:00:00Z', 'Not/AZone')).toBe(formatDate('2026-10-13T11:00:00Z'));
  });

  it('token dates use the server calendar day', () => {
    expect(formatDate('2026-10-13T11:00:00Z', SERVER_TZ)).toMatch(/Oct 14, 2026/);
  });
});
