import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, Archive, Check, Download, Lock, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { backupDownloadUrl, createBackup, deleteBackup, fetchBackupJob, fetchBackups } from '../../api/backups.ts';
import { BackupItem, BackupJob } from '../../types.ts';
import { formatBytes, formatLocalTimestamp } from '../../utils/formatters.ts';
import { useServerTimezone } from '../../utils/hooks.ts';
import { Badge } from '../common/Badge.tsx';
import { Button, buttonClass, IconButton } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { Checkbox } from '../common/Field.tsx';

/** How often the card asks for a running backup's progress. */
export const BACKUP_POLL_MS = 1000;

interface DatabaseBackupsCardProps {
  backupBeforeMigration: boolean;
  backupBeforeMigrationOverridden: boolean;
  onSaveBackupBeforeMigration: (value: boolean) => Promise<void>;
  /** After a backup is made or deleted (the Storage figures change). */
  onBackupsChanged?: () => void;
}

export function backupKindLabel(item: BackupItem): string {
  if (item.kind === 'pre-migration') {
    return `Before upgrade (v${item.schema_version} to v${item.target_schema})`;
  }
  return `Manual (v${item.schema_version})`;
}

export function expiryLabel(days: number): string {
  if (days <= 0) return 'Expires today';
  return days === 1 ? 'Expires in 1 day' : `Expires in ${days} days`;
}

function countLabel(count: number, bytes: number): string {
  if (count === 0) return 'none';
  return `${count} ${count === 1 ? 'backup' : 'backups'}, ${formatBytes(bytes)}`;
}

export const DatabaseBackupsCard: React.FC<DatabaseBackupsCardProps> = ({
  backupBeforeMigration,
  backupBeforeMigrationOverridden,
  onSaveBackupBeforeMigration,
  onBackupsChanged,
}) => {
  const serverTimezone = useServerTimezone();
  const [backups, setBackups] = useState<BackupItem[]>([]);
  const [totalBytes, setTotalBytes] = useState<number>(0);
  const [loaded, setLoaded] = useState<boolean>(false);
  const [job, setJob] = useState<BackupJob | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isStarting, setIsStarting] = useState<boolean>(false);
  const [isSavingToggle, setIsSavingToggle] = useState<boolean>(false);
  const [toDelete, setToDelete] = useState<BackupItem | null>(null);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const onChangedRef = useRef(onBackupsChanged);
  useEffect(() => {
    onChangedRef.current = onBackupsChanged;
  });

  const loadList = useCallback(async () => {
    try {
      const list = await fetchBackups();
      setBackups(list.backups);
      setTotalBytes(list.total_size_bytes);
      setLoaded(true);
    } catch (err: any) {
      setErrorMsg(err.message || 'Could not load the list of backups.');
    }
  }, []);

  useEffect(() => {
    loadList();
    // A backup started before this page was opened is still followed
    fetchBackupJob()
      .then((current) => {
        if (current.state === 'running') setJob(current);
      })
      .catch(() => {});
  }, [loadList]);

  const running = job?.state === 'running';

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(async () => {
      try {
        const current = await fetchBackupJob();
        if (current.state === 'running') {
          setJob(current);
          return;
        }
        setJob(null);
        if (current.state === 'done') {
          setNotice(`Backup written: ${current.filename} (${formatBytes(current.size_bytes ?? 0)}).`);
        } else if (current.state === 'failed') {
          setErrorMsg(current.error || 'The backup failed.');
        }
        await loadList();
        onChangedRef.current?.();
      } catch (err: any) {
        setJob(null);
        setErrorMsg(err.message || 'Lost track of the running backup.');
      }
    }, BACKUP_POLL_MS);
    return () => clearInterval(timer);
  }, [running, loadList]);

  const handleCreate = async () => {
    setErrorMsg(null);
    setNotice(null);
    setIsStarting(true);
    try {
      setJob(await createBackup());
    } catch (err: any) {
      setErrorMsg(err.message || 'Could not start the backup.');
    } finally {
      setIsStarting(false);
    }
  };

  const handleToggle = async (value: boolean) => {
    setErrorMsg(null);
    setIsSavingToggle(true);
    try {
      await onSaveBackupBeforeMigration(value);
    } catch (err: any) {
      setErrorMsg(err.message || 'Could not save the setting.');
    } finally {
      setIsSavingToggle(false);
    }
  };

  const handleDelete = async () => {
    if (!toDelete) return;
    setIsDeleting(true);
    setErrorMsg(null);
    setNotice(null);
    try {
      await deleteBackup(toDelete.filename);
      setToDelete(null);
      await loadList();
      onChangedRef.current?.();
    } catch (err: any) {
      setToDelete(null);
      setErrorMsg(err.message || 'Could not delete the backup.');
    } finally {
      setIsDeleting(false);
    }
  };

  const percent = job && job.total > 0 ? Math.min(100, Math.floor((job.done * 100) / job.total)) : null;
  const progressText =
    job?.stage === 'compressing' && job.total > 0
      ? `Compressing: ${formatBytes(job.done)} of ${formatBytes(job.total)} (${percent}%)`
      : 'Copying the database...';

  return (
    <Card data-testid="database-backups-card">
      <CardHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            Database Backups
            {loaded && <Badge code>{countLabel(backups.length, totalBytes)}</Badge>}
          </span>
        }
        icon={<Archive />}
        description="Archives in the backups folder beside the database. Each one is deleted 30 days after it was made."
        actions={
          <Button
            variant="primary"
            size="sm"
            onClick={handleCreate}
            disabled={running || isStarting}
            icon={running || isStarting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
          >
            {running ? 'Backing up...' : 'Create backup now'}
          </Button>
        }
      />

      <div className="space-y-1.5">
        <Checkbox
          label="Back up the database before upgrades"
          checked={backupBeforeMigration}
          disabled={backupBeforeMigrationOverridden || isSavingToggle}
          onChange={(e) => handleToggle(e.target.checked)}
        />
        <p className="text-[11px] text-fg-muted leading-relaxed">
          Recommended. Before a new version changes the database, LogShed saves a copy here, and stops the upgrade if
          there is no room for it. Logging carries on while you create a backup by hand.
        </p>
        {backupBeforeMigrationOverridden && (
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-warning-500/10 border border-warning-500/20 text-[11px] font-mono text-warning-300">
            <Lock className="w-3.5 h-3.5 text-warning-400 shrink-0" />
            <span>Locked by the AUTO_BACKUP_ON_MIGRATION environment variable</span>
          </div>
        )}
      </div>

      {running && (
        <div className="space-y-1.5" aria-live="polite">
          <p className="text-xs text-fg-soft font-mono">{progressText}</p>
          <div
            className="h-1.5 w-full rounded-full bg-raised overflow-hidden"
            role="progressbar"
            aria-label="Backup progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent ?? undefined}
          >
            <div
              className={`h-full bg-accent-500 transition-all${percent === null ? ' w-1/3 animate-pulse' : ''}`}
              style={percent === null ? undefined : { width: `${percent}%` }}
            />
          </div>
        </div>
      )}

      {notice && (
        <div className="p-3 bg-success-950/40 border border-success-800/60 rounded-lg flex items-start gap-2 text-xs text-success-300">
          <Check className="w-4 h-4 text-success-400 shrink-0 mt-0.5" />
          <span className="break-all">{notice}</span>
        </div>
      )}
      {errorMsg && (
        <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
          <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {loaded && backups.length === 0 && !running && (
        <p className="text-xs text-fg-muted">No backups yet.</p>
      )}
      {backups.length > 0 && (
        <ul className="divide-y divide-line border border-line rounded-lg" aria-label="Database backups">
          {backups.map((item) => (
            <li
              key={item.filename}
              className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 px-3 py-2.5"
            >
              <div className="min-w-0 space-y-0.5">
                <p className="text-xs font-medium text-fg">{backupKindLabel(item)}</p>
                <p className="text-[11px] text-fg-muted font-mono">
                  {formatLocalTimestamp(item.created_at, false, serverTimezone)}, {formatBytes(item.size_bytes)},{' '}
                  {expiryLabel(item.days_until_expiry).toLowerCase()}
                </p>
                <p className="text-[11px] text-fg-muted font-mono break-all">{item.filename}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <a
                  href={backupDownloadUrl(item.filename)}
                  download={item.filename}
                  className={buttonClass('secondary', 'sm')}
                  aria-label={`Download ${item.filename}`}
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Download</span>
                </a>
                <IconButton label={`Delete ${item.filename}`} variant="danger" onClick={() => setToDelete(item)}>
                  <Trash2 className="w-4 h-4" />
                </IconButton>
              </div>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        isOpen={toDelete !== null}
        title="Delete this backup?"
        onCancel={() => setToDelete(null)}
        onConfirm={handleDelete}
        confirmLabel={isDeleting ? 'Deleting...' : 'Delete backup'}
        confirmIcon={<Trash2 className="w-4 h-4" />}
        confirmDisabled={isDeleting}
        cancelDisabled={isDeleting}
        look="notice"
        testId="delete-backup-dialog"
      >
        <p className="text-sm">
          {toDelete ? backupKindLabel(toDelete) : ''}, {toDelete ? formatBytes(toDelete.size_bytes) : ''}
        </p>
        <p className="text-xs text-fg-muted break-all font-mono">{toDelete?.filename}</p>
        <p className="text-xs text-fg-muted">This cannot be undone.</p>
      </ConfirmDialog>
    </Card>
  );
};

export default DatabaseBackupsCard;
