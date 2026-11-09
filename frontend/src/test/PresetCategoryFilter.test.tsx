import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AlertPresetsModal } from '../components/alerts/AlertPresetsModal.tsx';
import { DropPresetsModal } from '../components/alerts/DropPresetsModal.tsx';
import { AlertPreset, DropPreset } from '../types.ts';
import { chooseOption, optionLabels, shownValue } from './selectDropdownHelpers.ts';

const alertPreset = (id: string, name: string, category?: string): AlertPreset => ({
  id,
  name,
  category,
  description: `${name} description`,
  rule_type: 'pattern',
  match_pattern: 'x',
  threshold_count: 1,
  window_seconds: 60,
  cooldown_seconds: 300,
  ai_enrichment: true,
});

const dropPreset = (id: string, name: string, category?: string): DropPreset => ({
  id,
  name,
  category,
  description: `${name} description`,
  message_pattern: 'x',
  is_regex: false,
});

const categoryField = () => screen.getByRole('button', { name: /^Category: / });
const cardNames = (names: string[]) => names.filter((n) => screen.queryByText(n) !== null);

function renderAlerts(presets: AlertPreset[], isOpen = true) {
  return render(
    <AlertPresetsModal
      isOpen={isOpen}
      onClose={vi.fn()}
      presets={presets}
      rules={[]}
      channels={[]}
      presetChannelId={null}
      onSelectChannelId={vi.fn()}
      onInstallPreset={vi.fn()}
      installingPresetId={null}
    />
  );
}

describe('preset category filter', () => {
  const alerts = [
    alertPreset('sys', 'Segfaults', 'System'),
    alertPreset('ssh', 'SSH brute force', 'Security'),
    alertPreset('mine', 'My own rule'),
    alertPreset('ups', 'UPS on battery', 'Power'),
  ];
  const names = alerts.map((p) => p.name);

  it('lists the categories in their set order with counts, and custom presets without one as Other', () => {
    renderAlerts(alerts);
    expect(shownValue(categoryField())).toBe('All categories (4)');
    expect(optionLabels(categoryField())).toEqual([
      'All categories (4)',
      'Security (1)',
      'Power (1)',
      'System (1)',
      'Other (1)',
    ]);
  });

  it('shows every preset, grouped by category, until one is chosen', () => {
    renderAlerts(alerts);
    const shown = screen.getAllByText(/description$/).map((el) => el.textContent);
    expect(shown).toEqual([
      'SSH brute force description',
      'UPS on battery description',
      'Segfaults description',
      'My own rule description',
    ]);
  });

  it('shows only the chosen category', () => {
    renderAlerts(alerts);
    chooseOption(categoryField(), 'Power (1)');
    expect(cardNames(names)).toEqual(['UPS on battery']);
    chooseOption(categoryField(), 'Other (1)');
    expect(cardNames(names)).toEqual(['My own rule']);
    chooseOption(categoryField(), 'All categories (4)');
    expect(cardNames(names)).toHaveLength(4);
  });

  it('goes back to all categories when the dialog is opened again', () => {
    const { rerender } = renderAlerts(alerts);
    chooseOption(categoryField(), 'Power (1)');
    const props = {
      onClose: vi.fn(),
      presets: alerts,
      rules: [],
      channels: [],
      presetChannelId: null,
      onSelectChannelId: vi.fn(),
      onInstallPreset: vi.fn(),
      installingPresetId: null,
    };
    rerender(<AlertPresetsModal isOpen={false} {...props} />);
    rerender(<AlertPresetsModal isOpen={true} {...props} />);
    expect(shownValue(categoryField())).toBe('All categories (4)');
    expect(cardNames(names)).toHaveLength(4);
  });

  it('filters the drop presets too', () => {
    const drops = [
      dropPreset('dns', 'DNS chatter', 'Network'),
      dropPreset('arr', 'Arr chatter', 'Apps'),
      dropPreset('mark', 'MARK lines', 'System'),
    ];
    render(
      <DropPresetsModal
        isOpen={true}
        onClose={vi.fn()}
        presets={drops}
        rules={[]}
        onInstallPreset={vi.fn()}
        installingPresetId={null}
      />
    );
    expect(optionLabels(categoryField())).toEqual(['All categories (3)', 'Network (1)', 'System (1)', 'Apps (1)']);
    chooseOption(categoryField(), 'Apps (1)');
    expect(cardNames(drops.map((d) => d.name))).toEqual(['Arr chatter']);
  });

  it('shows no filter when there are no presets', () => {
    renderAlerts([]);
    expect(screen.queryByRole('button', { name: /^Category: / })).toBeNull();
    expect(screen.getByText('No alert presets found.')).toBeInTheDocument();
  });
});
