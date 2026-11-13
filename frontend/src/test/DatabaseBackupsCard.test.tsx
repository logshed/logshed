import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as backupsApi from '../api/backups.ts';
import * as settingsApi from '../api/settings.ts';
import { BACKUP_POLL_MS, DatabaseBackupsCard, backupKindLabel, expiryLabel } from '../components/storage/DatabaseBackupsCard.tsx';
import { BackupItem, BackupJob } from '../types.ts';

const PRE: BackupItem = {
  filename: 'logshed-backup-pre-v3-to-v4-20261113T080000Z.tar.gz',
  kind: 'pre-migration',
  schema_version: 3,
  target_schema: 4,
  size_bytes: 300 * 1024 * 1024,
  created_at: '2026-11-13T08:00:00+00:00',
  expires_at: '2026-12-13T08:00:00+00:00',
  days_until_expiry: 30,
};
const MANUAL: BackupItem = {
  filename: 'logshed-backup-manual-v4-20261101T120000Z.tar.gz',
  kind: 'manual',
  schema_version: 4,
  target_schema: null,
  size_bytes: 10 * 1024 * 1024,
  created_at: '2026-11-01T12:00:00+00:00',
  expires_at: '2026-12-01T12:00:00+00:00',
  days_until_expiry: 1,
};
const IDLE: BackupJob = { state: 'idle', done: 0, total: 0 };

function renderCard(props: Partial<React.ComponentProps<typeof DatabaseBackupsCard>> = {}) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const onChanged = vi.fn();
  render(
    <DatabaseBackupsCard
      backupBeforeMigration
      backupBeforeMigrationOverridden={false}
      onSaveBackupBeforeMigration={onSave}
      onBackupsChanged={onChanged}
      {...props}
    />
  );
  return { onSave, onChanged };
}

describe('DatabaseBackupsCard', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({
      ai_provider: 'gemini',
      ai_model: 'm',
      retention_days: 14,
      server_timezone: 'UTC',
    } as any);
    vi.spyOn(backupsApi, 'fetchBackups').mockResolvedValue({ backups: [PRE, MANUAL], total_size_bytes: PRE.size_bytes + MANUAL.size_bytes });
    vi.spyOn(backupsApi, 'fetchBackupJob').mockResolvedValue(IDLE);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('lists each archive with its kind, size, time, expiry and a download link', async () => {
    renderCard();
    expect(await screen.findByText('2 backups, 310 MB')).toBeInTheDocument();
    const list = screen.getByRole('list', { name: 'Database backups' });
    const rows = within(list).getAllByRole('listitem');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('Before upgrade (v3 to v4)')).toBeInTheDocument();
    expect(within(rows[0]).getByText(/2026-11-13 08:00, 300 MB, expires in 30 days/)).toBeInTheDocument();
    expect(within(rows[1]).getByText('Manual (v4)')).toBeInTheDocument();
    expect(within(rows[1]).getByText(/expires in 1 day$/)).toBeInTheDocument();
    const link = within(rows[0]).getByRole('link', { name: `Download ${PRE.filename}` });
    expect(link).toHaveAttribute('href', `/api/system/backups/${PRE.filename}/download`);
    expect(link).toHaveAttribute('download', PRE.filename);
  });

  it('says when there are no backups', async () => {
    vi.spyOn(backupsApi, 'fetchBackups').mockResolvedValue({ backups: [], total_size_bytes: 0 });
    renderCard();
    expect(await screen.findByText('No backups yet.')).toBeInTheDocument();
    expect(screen.getByText('none')).toBeInTheDocument();
  });

  it('saves the toggle', async () => {
    const { onSave } = renderCard();
    const box = await screen.findByRole('checkbox', { name: 'Back up the database before upgrades' });
    expect(box).toBeChecked();
    fireEvent.click(box);
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(false));
  });

  it('locks the toggle when the environment variable is set', async () => {
    renderCard({ backupBeforeMigrationOverridden: true });
    const box = await screen.findByRole('checkbox', { name: 'Back up the database before upgrades' });
    expect(box).toBeDisabled();
    expect(screen.getByText('Locked by the AUTO_BACKUP_ON_MIGRATION environment variable')).toBeInTheDocument();
  });

  it('creates a backup, shows its progress and lists it when done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fresh = { ...MANUAL, filename: 'logshed-backup-manual-v4-20261113T090000Z.tar.gz', days_until_expiry: 30 };
    const create = vi.spyOn(backupsApi, 'createBackup').mockResolvedValue({ state: 'running', stage: 'copying', done: 0, total: 0 });
    const job = vi.spyOn(backupsApi, 'fetchBackupJob');
    const { onChanged } = renderCard();
    await screen.findByText('2 backups, 310 MB');

    fireEvent.click(screen.getByRole('button', { name: 'Create backup now' }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Copying the database...')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Backing up...' })).toBeDisabled();

    job.mockResolvedValueOnce({ state: 'running', stage: 'compressing', done: 50 * 1024 * 1024, total: 200 * 1024 * 1024 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKUP_POLL_MS);
    });
    expect(screen.getByText('Compressing: 50 MB of 200 MB (25%)')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Backup progress' })).toHaveAttribute('aria-valuenow', '25');

    job.mockResolvedValueOnce({ state: 'done', done: 0, total: 0, filename: fresh.filename, size_bytes: 12 * 1024 * 1024 });
    vi.spyOn(backupsApi, 'fetchBackups').mockResolvedValue({ backups: [fresh, PRE, MANUAL], total_size_bytes: 322 * 1024 * 1024 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKUP_POLL_MS);
    });
    expect(screen.getByText(`Backup written: ${fresh.filename} (12 MB).`)).toBeInTheDocument();
    expect(screen.getByText('3 backups, 322 MB')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create backup now' })).toBeEnabled();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('shows why a backup could not start', async () => {
    vi.spyOn(backupsApi, 'createBackup').mockRejectedValue(new Error('Not enough free space for a database backup'));
    renderCard();
    await screen.findByText('2 backups, 310 MB');
    fireEvent.click(screen.getByRole('button', { name: 'Create backup now' }));
    expect(await screen.findByText('Not enough free space for a database backup')).toBeInTheDocument();
  });

  it('shows a backup that failed while running', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(backupsApi, 'fetchBackupJob')
      .mockResolvedValueOnce({ state: 'running', stage: 'copying', done: 0, total: 0 })
      .mockResolvedValue({ state: 'failed', done: 0, total: 0, error: 'The backup failed: disk I/O error' });
    renderCard();
    expect(await screen.findByText('Copying the database...')).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKUP_POLL_MS);
    });
    expect(screen.getByText('The backup failed: disk I/O error')).toBeInTheDocument();
  });

  it('deletes an archive after confirmation', async () => {
    const remove = vi.spyOn(backupsApi, 'deleteBackup').mockResolvedValue(undefined);
    const { onChanged } = renderCard();
    await screen.findByText('2 backups, 310 MB');

    fireEvent.click(screen.getByRole('button', { name: `Delete ${MANUAL.filename}` }));
    const dialog = await screen.findByTestId('delete-backup-dialog');
    expect(within(dialog).getByText(MANUAL.filename)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByTestId('delete-backup-dialog')).not.toBeInTheDocument());
    expect(remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: `Delete ${MANUAL.filename}` }));
    vi.spyOn(backupsApi, 'fetchBackups').mockResolvedValue({ backups: [PRE], total_size_bytes: PRE.size_bytes });
    fireEvent.click(within(await screen.findByTestId('delete-backup-dialog')).getByRole('button', { name: 'Delete backup' }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(MANUAL.filename));
    expect(await screen.findByText('1 backup, 300 MB')).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('words kinds and expiry', () => {
    expect(backupKindLabel(PRE)).toBe('Before upgrade (v3 to v4)');
    expect(backupKindLabel(MANUAL)).toBe('Manual (v4)');
    expect(expiryLabel(0)).toBe('Expires today');
    expect(expiryLabel(1)).toBe('Expires in 1 day');
    expect(expiryLabel(12)).toBe('Expires in 12 days');
  });
});
