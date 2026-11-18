import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AlertRulesTab } from '../components/alerts/tabs/AlertRulesTab.tsx';
import type { MaintenanceWindowResponse } from '../types.ts';

vi.mock('../api/settings.ts', () => ({
  fetchSettings: vi.fn().mockResolvedValue({ server_timezone: 'UTC' }),
  getCachedServerTimezone: () => 'UTC',
}));

const renderTab = (maintenance: MaintenanceWindowResponse) =>
  render(
    <AlertRulesTab
      rules={[]}
      channels={[]}
      isMaintenanceActiveNow={true}
      maintenance={maintenance}
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
      onResetAllCounters={vi.fn()}
      getChannelStatus={() => ({ name: '', warning: null, isInvalid: false })}
    />
  );

const session = {
  session_id: 's', initiated_by: 'web_ui', log_handling: 'silence_alerts', target_host: 'nas',
  expires_at: '2030-01-01T00:00:00Z',
};

describe('AlertRulesTab maintenance banner', () => {
  it('names the target when only windows with a target run', () => {
    renderTab({ active: true, until: '2030-01-01T00:00:00Z', silences_all_alerts: false, sessions: [session] });
    expect(screen.getByText(/Logs from host nas count towards no alert rule/)).toBeInTheDocument();
    expect(screen.getByText(/Other alerts are sent as usual\./)).toBeInTheDocument();
    expect(screen.queryByText(/Alert notifications are silenced/)).toBeNull();
  });

  it('says every notification is silenced when a window covers every source', () => {
    renderTab({ active: true, until: '2030-01-01T00:00:00Z', silences_all_alerts: true, sessions: [session] });
    expect(screen.getByText(/Alert notifications are silenced/)).toBeInTheDocument();
  });
});
