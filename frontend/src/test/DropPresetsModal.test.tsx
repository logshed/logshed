import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DropPresetsModal } from '../components/alerts/DropPresetsModal.tsx';
import { DropPreset } from '../types.ts';

describe('DropPresetsModal severity wording (WP2)', () => {
  it('shows the severity range with "and less severe"', () => {
    const preset: DropPreset = {
      id: 'p1',
      name: 'Quiet debug',
      description: 'Drops debug chatter',
      app_pattern: 'cron',
      message_pattern: 'session opened',
      is_regex: false,
      severity_threshold: 6,
    };
    render(
      <DropPresetsModal
        isOpen={true}
        onClose={vi.fn()}
        presets={[preset]}
        rules={[]}
        onInstallPreset={vi.fn()}
        installingPresetId={null}
      />
    );
    expect(screen.getByText('Info (6) and less severe')).toBeInTheDocument();
    expect(screen.queryByText(/and below/)).toBeNull();
  });
});
