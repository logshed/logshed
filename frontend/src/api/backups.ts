import { apiFetch } from './client.ts';
import { BackupJob, BackupListResponse, RecoveryDetails } from '../types.ts';

export async function fetchBackups(): Promise<BackupListResponse> {
  return apiFetch<BackupListResponse>('/api/system/backups');
}

export async function fetchBackupJob(): Promise<BackupJob> {
  return apiFetch<BackupJob>('/api/system/backups/current');
}

/** Starts a backup; the job runs on and is followed with fetchBackupJob. */
export async function createBackup(): Promise<BackupJob> {
  return apiFetch<BackupJob>('/api/system/backups', { method: 'POST' });
}

export async function deleteBackup(filename: string): Promise<void> {
  await apiFetch<{ status: string }>(`/api/system/backups/${encodeURIComponent(filename)}`, { method: 'DELETE' });
}

/**
 * The archive's address, for a plain link: the browser streams it to disk with the session cookie.
 * Never fetched into a blob, because an archive can be many gigabytes.
 */
export function backupDownloadUrl(filename: string): string {
  return `/api/system/backups/${encodeURIComponent(filename)}/download`;
}

export async function fetchRecovery(): Promise<RecoveryDetails> {
  return apiFetch<RecoveryDetails>('/api/system/recovery');
}

export async function retryUpgrade(skipBackup: boolean): Promise<void> {
  await apiFetch<{ status: string }>(skipBackup ? '/api/system/recovery/skip-backup' : '/api/system/recovery/retry', {
    method: 'POST',
  });
}
