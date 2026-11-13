import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as authApi from '../api/auth.ts';
import * as backupsApi from '../api/backups.ts';
import * as systemApi from '../api/system.ts';
import { AuthProvider } from '../context/AuthContext.tsx';
import { HEALTH_POLL_MS } from '../components/recovery/RecoveryScreen.tsx';
import { StartupGate } from '../components/recovery/StartupGate.tsx';
import { upgradeProgressText } from '../components/recovery/upgradeProgress.ts';
import { HealthResponse, RecoveryDetails } from '../types.ts';

const FAILED: HealthResponse = {
  status: 'migration_failed',
  upgrade: { stage: 'migration', from_schema: 3, target_schema: 4, failed_at: '2026-11-13T08:00:00+00:00' },
};
const DETAILS: RecoveryDetails = {
  status: 'recovery',
  app_version: '1.4.0',
  stage: 'migration',
  from_schema: 3,
  target_schema: 4,
  current_schema: 3,
  failed_at: '2026-11-13T08:00:00+00:00',
  error: 'UNIQUE constraint failed: saved_views.name',
  backup_filename: 'logshed-backup-pre-v3-to-v4-20261113T075900Z.tar.gz',
  backup_available: true,
  disk_free_bytes: 40 * 1024 ** 3,
  disk_total_bytes: 100 * 1024 ** 3,
  database_size_bytes: 2 * 1024 ** 3,
  backup_needed_bytes: 600 * 1024 ** 2,
  backup_floor_bytes: 10 * 1024 ** 3,
  server_timezone: 'UTC',
};

function renderGate() {
  return render(
    <AuthProvider>
      <StartupGate>
        <div>The app</div>
      </StartupGate>
    </AuthProvider>
  );
}

describe('StartupGate and RecoveryScreen', () => {
  let reload: ReturnType<typeof vi.fn>;
  const originalLocation = window.location;

  beforeEach(() => {
    vi.restoreAllMocks();
    reload = vi.fn();
    Object.defineProperty(window, 'location', { configurable: true, value: { ...originalLocation, reload } });
    vi.spyOn(authApi, 'getAuthStatus').mockResolvedValue({ authenticated: true, setup_required: false });
    vi.spyOn(backupsApi, 'fetchRecovery').mockResolvedValue(DETAILS);
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
  });

  it('shows the app when LogShed is running', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({ status: 'ok' });
    renderGate();
    expect(await screen.findByText('The app')).toBeInTheDocument();
  });

  it('shows the app when the health check fails', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockRejectedValue(new Error('network'));
    renderGate();
    expect(await screen.findByText('The app')).toBeInTheDocument();
  });

  it('asks for sign-in first, without the error', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue(FAILED);
    vi.spyOn(authApi, 'getAuthStatus').mockResolvedValue({ authenticated: false, setup_required: false });
    renderGate();
    expect(await screen.findByText(/could not upgrade its database from schema 3 to\s+4/)).toBeInTheDocument();
    expect(screen.getByText(/A step of the upgrade failed and was rolled back/)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.queryByText('The app')).not.toBeInTheDocument();
    expect(backupsApi.fetchRecovery).not.toHaveBeenCalled();
  });

  it('shows the error, the schema, the disk and the backup once signed in', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue(FAILED);
    renderGate();
    expect(await screen.findByText(DETAILS.error!)).toBeInTheDocument();
    expect(screen.getByText(/could not upgrade the database from schema 3 to 4/)).toHaveTextContent('(2026-11-13 08:00:00)');
    expect(screen.getByText('Your database is unchanged at schema 3.')).toBeInTheDocument();
    expect(screen.getByText(/Disk: 40 GB free of 100 GB/)).toHaveTextContent('A backup needs about 600 MB and must leave 10 GB free.');
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute(
      'href',
      `/api/system/backups/${DETAILS.backup_filename}/download`
    );
    expect(screen.getByText(/The backup above still matches the database, so no new one is made/)).toBeInTheDocument();
    expect(screen.queryByText('The app')).not.toBeInTheDocument();
  });

  it('retries, follows the progress and opens LogShed when it is ready', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const health = vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue(FAILED);
    const retry = vi.spyOn(backupsApi, 'retryUpgrade').mockResolvedValue(undefined);
    renderGate();
    await screen.findByText(DETAILS.error!);
    fireEvent.click(screen.getByRole('button', { name: 'Retry the upgrade' }));
    await waitFor(() => expect(retry).toHaveBeenCalledWith(false));
    expect(await screen.findByRole('heading', { name: 'LogShed is upgrading its database' })).toBeInTheDocument();

    health.mockResolvedValueOnce({ status: 'upgrading', upgrade: { stage: 'migrate', done: 0, total: 1, from_schema: 3, target_schema: 4 } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEALTH_POLL_MS);
    });
    expect(screen.getByText('Upgrading the database from schema 3 to 4 (step 1 of 1)')).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();

    health.mockResolvedValueOnce({ status: 'ok' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEALTH_POLL_MS);
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('shows the recovery screen again when the retry fails too', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const health = vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue(FAILED);
    vi.spyOn(backupsApi, 'retryUpgrade').mockResolvedValue(undefined);
    renderGate();
    await screen.findByText(DETAILS.error!);
    fireEvent.click(screen.getByRole('button', { name: 'Retry the upgrade' }));
    await screen.findByRole('heading', { name: 'LogShed is upgrading its database' });
    health.mockResolvedValueOnce(FAILED);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEALTH_POLL_MS);
    });
    await waitFor(() => expect(backupsApi.fetchRecovery).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(DETAILS.error!)).toBeInTheDocument();
  });

  it('upgrades without a backup only after confirmation', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue({ ...FAILED, upgrade: { ...FAILED.upgrade!, stage: 'backup' } });
    vi.spyOn(backupsApi, 'fetchRecovery').mockResolvedValue({
      ...DETAILS,
      stage: 'backup',
      error: 'Not enough free space for a database backup',
      backup_filename: null,
      backup_available: false,
    });
    const retry = vi.spyOn(backupsApi, 'retryUpgrade').mockResolvedValue(undefined);
    renderGate();
    expect(await screen.findByText('No backup was made for this upgrade.')).toBeInTheDocument();
    expect(screen.getByText(/Fix the cause shown above \(most often too little free space\)/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Upgrade without a backup' }));
    const dialog = await screen.findByTestId('skip-backup-dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(retry).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Upgrade without a backup' }));
    fireEvent.click(within(await screen.findByTestId('skip-backup-dialog')).getByRole('button', { name: 'Upgrade without a backup' }));
    await waitFor(() => expect(retry).toHaveBeenCalledWith(true));
  });

  it('shows why a retry was refused', async () => {
    vi.spyOn(systemApi, 'fetchHealth').mockResolvedValue(FAILED);
    vi.spyOn(backupsApi, 'retryUpgrade').mockRejectedValue(new Error('LogShed is not in recovery mode.'));
    renderGate();
    await screen.findByText(DETAILS.error!);
    fireEvent.click(screen.getByRole('button', { name: 'Retry the upgrade' }));
    expect(await screen.findByText('LogShed is not in recovery mode.')).toBeInTheDocument();
  });

  it('words the progress as the status page does', () => {
    expect(upgradeProgressText({ stage: 'backup', done: 512 * 1024 ** 2, total: 2 * 1024 ** 3, from_schema: 2, target_schema: 3 })).toBe(
      'Backing up the database: 512 MB of 2 GB (25%)'
    );
    expect(upgradeProgressText({ stage: 'backup', from_schema: 2, target_schema: 3 })).toBe('Backing up the database');
    expect(upgradeProgressText({ stage: 'starting', from_schema: 2, target_schema: 3 })).toBe('Starting LogShed');
  });
});
