import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { StoragePanel } from '../components/storage/StoragePanel.tsx';
import * as settingsApi from '../api/settings.ts';
import * as systemApi from '../api/system.ts';

describe('StoragePanel Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'gemini-3.7-flash',
      ai_api_key: '********',
      ai_base_url: null,
      retention_days: 14,
      max_retention_days: 30,
      has_ai_api_key: true,
    });

    vi.spyOn(systemApi, 'fetchStorageMetrics').mockResolvedValue({
      db_size_bytes: 18247000000,
      disk_free_bytes: 450000000000,
      disk_total_bytes: 1000000000000,
      history: [],
    });
  });

  it('renders storage metrics, retention slider, and storage trend chart', async () => {
    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: /Storage & Retention/i })).toBeInTheDocument();
    });

    expect(screen.getByTestId('db-size-display')).toHaveTextContent('16.99 GB');
    expect(screen.getByTestId('disk-free-display')).toHaveTextContent('419.1 GB');
    expect(screen.getByText(/Log Retention Policy/i)).toBeInTheDocument();
  });

  it('updates retention policy through RetentionSlider', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: /Storage & Retention/i })).toBeInTheDocument();
    });

    const slider = screen.getByRole('slider');
    fireEvent.change(slider, { target: { value: '21' } });

    const saveBtn = screen.getByRole('button', { name: /Save Retention/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ retention_days: 21 });
    });
  });

  it('renders error message when loading settings or metrics fails', async () => {
    vi.spyOn(systemApi, 'fetchStorageMetrics').mockRejectedValue(new Error('Storage disk unreadable'));

    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByText(/Storage disk unreadable/i)).toBeInTheDocument();
    });
  });

  it('triggers compaction from StoragePanel and refreshes metrics upon completion', async () => {
    const fetchStorageSpy = vi.spyOn(systemApi, 'fetchStorageMetrics');
    const vacuumSpy = vi.spyOn(systemApi, 'triggerVacuum').mockResolvedValue({
      status: 'ok',
      previous_size_bytes: 18247000000,
      new_size_bytes: 12000000000,
      reclaimed_bytes: 6247000000,
      metrics: {
        recorded_at: '2026-09-28T00:00:00Z',
        db_size_bytes: 12000000000,
        disk_free_bytes: 456000000000,
        disk_total_bytes: 1000000000000,
        total_logs_count: 50000,
      },
    });

    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: /Storage & Retention/i })).toBeInTheDocument();
    });

    const compactBtn = screen.getByRole('button', { name: /Compact Database/i });
    fireEvent.click(compactBtn);

    const confirmBtn = screen.getByRole('button', { name: /Confirm & Compact/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(vacuumSpy).toHaveBeenCalledTimes(1);
      expect(screen.getByText('Database Compaction Completed Successfully:')).toBeInTheDocument();
      expect(fetchStorageSpy).toHaveBeenCalledTimes(2);
    });
  });
});
