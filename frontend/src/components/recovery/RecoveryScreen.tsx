import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, AlertTriangle, Download, LogOut, RefreshCw } from 'lucide-react';
import { backupDownloadUrl, fetchRecovery, retryUpgrade } from '../../api/backups.ts';
import { fetchHealth } from '../../api/system.ts';
import { useAuth } from '../../context/AuthContext.tsx';
import { HealthResponse, RecoveryDetails } from '../../types.ts';
import { formatBytes, formatLocalTimestamp } from '../../utils/formatters.ts';
import { Button, buttonClass } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { LoginForm } from '../auth/LoginForm.tsx';
import { LogShedLogo } from '../common/LogShedLogo.tsx';
import { upgradeProgressText } from './upgradeProgress.ts';

/** How often the screen asks how a retried upgrade is going. */
export const HEALTH_POLL_MS = 2000;

function stoppedSentence(stage: string | null | undefined): string {
  return stage === 'backup'
    ? 'The backup LogShed makes before an upgrade could not be made, so the upgrade did not start.'
    : 'A step of the upgrade failed and was rolled back.';
}

const UpgradingView: React.FC<{ health: HealthResponse }> = ({ health }) => (
  <Card>
    <CardHeader as="h2" icon={<RefreshCw />} title="LogShed is upgrading its database" />
    <p className="text-sm text-accent-400 font-mono" aria-live="polite">
      {upgradeProgressText(health.upgrade)}
    </p>
    <p className="text-xs text-fg-soft">
      <strong>Do not restart or stop the container.</strong> LogShed opens by itself when the upgrade is done.
    </p>
  </Card>
);

export const RecoveryScreen: React.FC<{ initialHealth: HealthResponse }> = ({ initialHealth }) => {
  const { isAuthenticated, isLoading, logout } = useAuth();
  const [health, setHealth] = useState<HealthResponse>(initialHealth);
  const [details, setDetails] = useState<RecoveryDetails | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isSending, setIsSending] = useState<boolean>(false);
  const [confirmSkip, setConfirmSkip] = useState<boolean>(false);

  const upgrading = health.status === 'upgrading';

  const loadDetails = useCallback(async () => {
    try {
      const next = await fetchRecovery();
      if (next.status === 'recovery') {
        setDetails(next);
      } else {
        // Another tab already retried: follow the upgrade (or open LogShed if it is done)
        const current = await fetchHealth();
        if (current.status === 'upgrading' || current.status === 'migration_failed') setHealth(current);
        else window.location.reload();
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Could not load what went wrong.');
    }
  }, []);

  useEffect(() => {
    if (isAuthenticated && !upgrading) loadDetails();
  }, [isAuthenticated, upgrading, loadDetails]);

  // Follow a running upgrade until LogShed is ready (then open it) or stops again
  useEffect(() => {
    if (!upgrading) return;
    const timer = setInterval(async () => {
      try {
        const next = await fetchHealth();
        if (next.status === 'upgrading' || next.status === 'migration_failed') {
          setHealth(next);
        } else {
          window.location.reload();
        }
      } catch {
        // The server may be restarting; ask again at the next tick
      }
    }, HEALTH_POLL_MS);
    return () => clearInterval(timer);
  }, [upgrading]);

  const startUpgrade = async (skipBackup: boolean) => {
    setConfirmSkip(false);
    setErrorMsg(null);
    setIsSending(true);
    try {
      await retryUpgrade(skipBackup);
      setDetails(null);
      setHealth({
        status: 'upgrading',
        upgrade: { stage: skipBackup ? 'migrate' : 'backup', from_schema: details?.current_schema ?? 0, target_schema: details?.target_schema ?? 0 },
      });
    } catch (err: any) {
      setErrorMsg(err.message || 'Could not start the upgrade.');
    } finally {
      setIsSending(false);
    }
  };

  const shell = (content: React.ReactNode) => (
    <main className="min-h-screen bg-canvas flex flex-col items-center p-3 sm:p-6">
      <div className="w-full max-w-2xl space-y-4">
        <div className="flex items-center gap-2 pt-2">
          <LogShedLogo className="w-6 h-6 text-accent-600" />
          <h1 className="text-base font-bold text-fg-strong">LogShed</h1>
          {isAuthenticated && (
            <Button size="sm" variant="ghost" className="ml-auto" onClick={logout} icon={<LogOut className="w-3.5 h-3.5" />}>
              Sign out
            </Button>
          )}
        </div>
        {content}
      </div>
    </main>
  );

  if (upgrading) return shell(<UpgradingView health={health} />);

  const stage = health.upgrade?.stage;
  if (isLoading) return shell(<p className="text-xs text-fg-muted font-mono">Loading...</p>);
  if (!isAuthenticated) {
    return (
      <LoginForm
        notice={
          <div className="p-3 bg-warning-950/40 border border-warning-800/60 rounded-lg flex items-start gap-2 text-xs text-warning-300">
            <AlertTriangle className="w-4 h-4 text-warning-400 shrink-0 mt-0.5" />
            <span>
              LogShed could not upgrade its database from schema {health.upgrade?.from_schema} to{' '}
              {health.upgrade?.target_schema}. {stoppedSentence(stage)} Sign in to see what went wrong and what you can
              do. Logs are not being received until then.
            </span>
          </div>
        }
      />
    );
  }

  const d = details;
  const failedAt = d?.failed_at ? formatLocalTimestamp(d.failed_at, true, d.server_timezone ?? undefined) : '';
  const backupStage = (d?.stage ?? stage) === 'backup';
  // A run across several schemas can stop part way: the steps before the failed one stay committed
  const moved = Boolean(d && d.current_schema != null && d.current_schema !== d.from_schema);
  // The server reuses the backup only while the database is still at the schema it holds
  const backupReused = Boolean(d?.backup_available && d.current_schema === d.from_schema);

  return shell(
    <>
      <Card>
        <CardHeader as="h2" icon={<AlertTriangle />} title="The database upgrade stopped" />
        {d ? (
          <div className="space-y-3 text-xs text-fg-soft">
            <p className="text-sm text-fg">
              LogShed {d.app_version} could not upgrade the database from schema {d.from_schema} to {d.target_schema}
              {failedAt && <> ({failedAt})</>}. {stoppedSentence(d.stage)}
            </p>
            <p>
              <strong className="text-fg">
                {moved
                  ? `Your database is now at schema ${d.current_schema}: the steps up to it finished, and the next one was rolled back.`
                  : `Your database is unchanged at schema ${d.current_schema}.`}
              </strong>{' '}
              LogShed is not receiving or storing logs until you retry the upgrade or go back to the version you ran before.
            </p>
            {d.error && (
              <div>
                <p className="text-fg-muted mb-1">The error:</p>
                <pre className="p-2.5 bg-canvas border border-line rounded-lg font-mono text-[11px] text-danger-300 whitespace-pre-wrap break-words">
                  {d.error}
                </pre>
              </div>
            )}
            {d.disk_free_bytes != null && d.disk_total_bytes != null && (
              <p className="font-mono text-[11px] text-fg-muted">
                Disk: {formatBytes(d.disk_free_bytes)} free of {formatBytes(d.disk_total_bytes)}. Database:{' '}
                {formatBytes(d.database_size_bytes ?? 0)}. A backup needs about {formatBytes(d.backup_needed_bytes ?? 0)}{' '}
                and must leave {formatBytes(d.backup_floor_bytes ?? 0)} free.
              </p>
            )}
            {d.backup_available && d.backup_filename ? (
              <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                <p className="min-w-0 break-all">
                  Backup made before this upgrade: <span className="font-mono">{d.backup_filename}</span>
                </p>
                <a
                  href={backupDownloadUrl(d.backup_filename)}
                  download={d.backup_filename}
                  className={`${buttonClass('secondary', 'sm')} self-start shrink-0`}
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Download</span>
                </a>
              </div>
            ) : (
              <p>No backup was made for this upgrade.</p>
            )}
          </div>
        ) : (
          <p className="text-xs text-fg-muted font-mono">Loading what went wrong...</p>
        )}
      </Card>

      {errorMsg && (
        <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
          <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      <Card>
        <CardHeader as="h2" title="What you can do" />
        <div className="space-y-4 text-xs text-fg-soft">
          <div className="space-y-1.5">
            <Button variant="primary" onClick={() => startUpgrade(false)} disabled={isSending || !d}>
              Retry the upgrade
            </Button>
            <p className="text-fg-muted">
              {backupStage
                ? 'Fix the cause shown above (most often too little free space), then retry: LogShed tries the backup again before the upgrade.'
                : backupReused
                  ? 'Runs the upgrade again. The backup above still matches the database, so no new one is made.'
                  : 'Runs the upgrade again, backing up the database first if backups before upgrades are on.'}
            </p>
          </div>
          <div className="space-y-1.5">
            <Button variant={backupStage ? 'danger' : 'danger-soft'} onClick={() => setConfirmSkip(true)} disabled={isSending || !d}>
              Upgrade without a backup
            </Button>
            <p className="text-fg-muted">For a disk with no room for a backup. Without one, going back later needs a copy of the data folder.</p>
          </div>
          <div className="space-y-1.5">
            <p className="text-fg font-medium">Or go back to the version you ran before</p>
            <p className="text-fg-muted">
              {moved
                ? `The version you ran before cannot run on schema ${d?.current_schema}. Stop the container, restore the backup made before this upgrade with the restore command ("Restoring a backup" in the configuration guide), then start the version you ran before.`
                : `Set the container's image tag to the version you ran before this upgrade and start it again. That works while the database is still at schema ${d?.current_schema ?? health.upgrade?.from_schema}, as it is now. See "Going back to an older version" in the configuration guide.`}
            </p>
          </div>
        </div>
      </Card>

      <ConfirmDialog
        isOpen={confirmSkip}
        title="Upgrade without a backup?"
        onCancel={() => setConfirmSkip(false)}
        onConfirm={() => startUpgrade(true)}
        confirmLabel="Upgrade without a backup"
        look="notice"
        testId="skip-backup-dialog"
      >
        <p className="text-sm">The database is upgraded with no copy of it made first.</p>
        <p className="text-xs text-fg-muted">
          If the upgrade goes wrong, or you want to go back to the version you ran before, you will need a copy of the data
          folder you made yourself.
        </p>
      </ConfirmDialog>
    </>
  );
};

export default RecoveryScreen;
