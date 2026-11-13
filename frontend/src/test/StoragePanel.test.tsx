import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { StoragePanel } from '../components/storage/StoragePanel.tsx';
import * as settingsApi from '../api/settings.ts';
import * as systemApi from '../api/system.ts';
import * as logsApi from '../api/logs.ts';
import * as backupsApi from '../api/backups.ts';

describe('StoragePanel Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({
      sources: ['host-1'],
      apps: ['app-1'],
      host_to_apps: {},
      app_to_hosts: {},
    });
    vi.spyOn(logsApi, 'previewDeleteLogs').mockResolvedValue({
      matched_count: 0,
    });
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
      backups_size_bytes: 3 * 1024 ** 3,
      backups_count: 2,
      history: [],
    });
    vi.spyOn(backupsApi, 'fetchBackups').mockResolvedValue({ backups: [], total_size_bytes: 0 });
    vi.spyOn(backupsApi, 'fetchBackupJob').mockResolvedValue({ state: 'idle', done: 0, total: 0 });
  });

  it('shows backups as their own line and the backups card between retention and the trend chart', async () => {
    render(<StoragePanel />);
    expect(await screen.findByTestId('backups-size-display')).toHaveTextContent('Backups: 3 GB');
    const card = await screen.findByTestId('database-backups-card');
    const retention = screen.getByText(/Log Retention Policy/i);
    expect(retention.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(await screen.findByText('No backups yet.')).toBeInTheDocument();
  });

  it('lists the day files between the retention card and the backups card', async () => {
    vi.spyOn(systemApi, 'fetchStorageMetrics').mockResolvedValue({
      db_size_bytes: 3 * 1024 ** 3,
      main_db_size_bytes: 1024 ** 3,
      day_files_size_bytes: 2 * 1024 ** 3,
      disk_free_bytes: 450000000000,
      disk_total_bytes: 1000000000000,
      retention_days: 14,
      log_files: [
        { day: '2026-11-13', file_name: 'logs-2026-11-13-UTC.db', state: 'open', logs: 5, size_bytes: 1024 ** 3, expires_at: '2026-11-28T00:00:00+00:00' },
        { day: '2026-11-12', file_name: 'logs-2026-11-12-UTC.db', state: 'sealed', logs: 9, size_bytes: 1024 ** 3, expires_at: '2026-11-27T00:00:00+00:00' },
      ],
      history: [],
    });
    render(<StoragePanel />);
    const card = await screen.findByTestId('log-files-card');
    expect(screen.getByTestId('db-split-display')).toHaveTextContent('logs.db 1 GB + 2 day files 2 GB');
    const retention = screen.getByText(/Log Retention Policy/i);
    expect(retention.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(card.compareDocumentPosition(screen.getByTestId('database-backups-card')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders storage metrics, retention slider, and storage trend chart', async () => {
    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: /Storage & Retention/i })).toBeInTheDocument();
    });

    expect(screen.getByTestId('db-size-display')).toHaveTextContent('16.99 GB');
    expect(screen.getByTestId('disk-free-display')).toHaveTextContent('419.1 GB');
    expect(screen.getByText(/Log Retention Policy/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 3, name: /Targeted Log Deletion/i })).toBeInTheDocument();
  });

  it('forces a fresh settings fetch when the panel mounts', async () => {
    const fetchSpy = vi.spyOn(settingsApi, 'fetchSettings');
    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: /Storage & Retention/i })).toBeInTheDocument();
    });
    expect(fetchSpy).toHaveBeenCalledWith(true);
  });

  it('updates retention policy through RetentionSlider', async () => {
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });

    render(<StoragePanel />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: /Storage & Retention/i })).toBeInTheDocument();
    });
    // The slider copies the stored retention in an effect after it mounts; let that run first, or a
    // slow runner can apply it after the change below and put the slider back to 14
    await act(async () => {});

    const slider = screen.getByRole('slider');
    fireEvent.change(slider, { target: { value: '21' } });
    expect(slider).toHaveValue('21');

    const saveBtn = screen.getByRole('button', { name: /Save Retention/i });
    await waitFor(() => {
      expect(saveBtn).not.toBeDisabled();
    });
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
