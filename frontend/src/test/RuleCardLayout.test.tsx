import { render, screen, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DropRulesTab } from '../components/alerts/tabs/DropRulesTab.tsx';
import { AlertRulesTab } from '../components/alerts/tabs/AlertRulesTab.tsx';
import * as dropRulesApi from '../api/dropRules.ts';
import { AlertRule, DropRule } from '../types.ts';

vi.mock('../api/settings.ts', () => ({
  fetchSettings: vi.fn().mockResolvedValue({ server_timezone: 'UTC' }),
  getCachedServerTimezone: () => 'UTC',
}));

const dropRule = (id: number, dropped_count: number): DropRule => ({
  id,
  name: `Drop ${id}`,
  source_pattern: null,
  app_pattern: null,
  message_pattern: `noise ${id}`,
  is_regex: false,
  is_enabled: true,
  severity_threshold: null,
  dropped_count,
  created_at: '2026-11-18T10:00:00Z',
});

const alertRule = (id: number, trigger_count: number): AlertRule => ({
  id,
  name: `Alert ${id}`,
  rule_type: 'threshold',
  channel_id: null,
  filter_app: null,
  filter_severity: null,
  match_pattern: `boom ${id}`,
  threshold_count: 1,
  window_seconds: 60,
  cooldown_seconds: 300,
  ai_enrichment: false,
  is_enabled: true,
  trigger_count,
  last_triggered_at: trigger_count ? '2026-11-18T09:00:00Z' : null,
  suppress_until: null,
  created_at: '2026-11-18T08:00:00Z',
});

function renderAlertTab(rules: AlertRule[]) {
  render(
    <AlertRulesTab
      rules={rules}
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
      onResetAllCounters={vi.fn()}
      getChannelStatus={() => ({ name: 'None', warning: null, isInvalid: false })}
    />
  );
}

// A long name with no spaces ran off a phone screen, and the badges sat beside or below the name
// depending on its length. The name now has a line of its own that wraps anywhere, and the badges a row below it.
const LONG = 'Drop_chatter_from_my_very_long_container_name_on_the_backup_host_every_night';

/** The name's element and the row holding the badge: they must be separate, with the name outside the row. */
function expectNameAboveBadges(badge: HTMLElement) {
  const name = screen.getByText(LONG);
  expect(name.className).toContain('wrap-anywhere');
  const badgeRow = badge.closest('.flex-wrap');
  expect(badgeRow).not.toBeNull();
  expect(badgeRow?.contains(name)).toBe(false);
  expect(name.nextElementSibling).toBe(badgeRow);
}

describe('Rule cards: name on its own line, badges below', () => {
  it('drop rules', async () => {
    vi.spyOn(dropRulesApi, 'fetchDropRules').mockResolvedValue([{ ...dropRule(1, 158), name: LONG }, dropRule(2, 2)]);
    render(<DropRulesTab />);
    await screen.findByText(LONG);
    const row = within(screen.getByTestId('drop-rule-row-1'));
    expectNameAboveBadges(row.getByText('158 dropped'));
    expectNameAboveBadges(row.getByText('Substring'));
  });

  it('alert rules', () => {
    renderAlertTab([{ ...alertRule(1, 3), name: LONG, ai_enrichment: true }]);
    expectNameAboveBadges(screen.getByText('threshold'));
    expectNameAboveBadges(screen.getByText('Fired 3x'));
    expectNameAboveBadges(screen.getByText('AI Enriched'));
  });
});
