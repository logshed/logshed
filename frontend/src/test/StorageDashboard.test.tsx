import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { StorageCard } from '../components/storage/StorageCard.tsx';
import { formatBytes } from '../utils/formatters.ts';
import { StorageMetricsResponse } from '../types.ts';

describe('StorageCard Component', () => {
  it('formats byte sizes correctly', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1024 * 1024 * 50)).toBe('50 MB');
    expect(formatBytes(1024 * 1024 * 1024 * 2.5)).toBe('2.5 GB');
  });

  it('renders mock storage metrics with correct calculations', () => {
    const mockMetrics: StorageMetricsResponse = {
      db_size_bytes: 18247000000,      // 16.99 GB
      disk_free_bytes: 450000000000,    // 419.1 GB free
      disk_total_bytes: 1000000000000,  // 931.32 GB total
      history: [
        {
          recorded_at: '2026-08-29T12:00:00Z',
          db_size_bytes: 18247000000,
          disk_free_bytes: 450000000000,
          disk_total_bytes: 1000000000000,
          total_logs_count: 26000000,
        },
      ],
    };

    render(<StorageCard metrics={mockMetrics} />);

    // DB size display
    expect(screen.getByTestId('db-size-display')).toHaveTextContent('16.99 GB');
    // Disk free display
    expect(screen.getByTestId('disk-free-display')).toHaveTextContent('419.1 GB');
    // Total logs count
    expect(screen.getByText('26,000,000')).toBeInTheDocument();

    // Progress bar width: used = (1000 - 450) / 1000 = 55%
    const progressBar = screen.getByTestId('disk-usage-bar');
    expect(progressBar).toHaveStyle({ width: '55%' });
  });
});

describe('StorageCard helper text contrast', () => {
  it('uses brighter 11px text for the caption lines', () => {
    const metrics: StorageMetricsResponse = {
      db_size_bytes: 1024,
      disk_free_bytes: 2048,
      disk_total_bytes: 4096,
      history: [
        {
          recorded_at: '2026-08-29T12:00:00Z',
          db_size_bytes: 1024,
          disk_free_bytes: 2048,
          disk_total_bytes: 4096,
          total_logs_count: 10,
        },
      ],
    };
    render(<StorageCard metrics={metrics} />);
    for (const caption of [screen.getByText('logs.db + WAL + SHM'), screen.getByText('Indexed in FTS5')]) {
      expect(caption.className).toContain('text-fg-muted');
      expect(caption.className).toContain('text-[11px]');
      expect(caption.className).not.toContain('text-fg-subtle');
      expect(caption.className).not.toContain('text-[10px]');
    }
  });
});
