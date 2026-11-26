import React, { useRef, useState } from 'react';
import { Download, FileJson, KeyRound, Upload } from 'lucide-react';
import {
  ConfigImportPreview,
  ConfigSection,
  ConfigSectionResult,
  exportConfiguration,
  importConfiguration,
  previewConfigurationImport,
} from '../../api/config.ts';
import { downloadBlob, formatLocalTimestamp } from '../../utils/formatters.ts';
import { Badge } from '../common/Badge.tsx';
import { Button } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { Checkbox, TextField } from '../common/Field.tsx';
import { Modal } from '../common/Modal.tsx';
import { FeedbackBanner, type Feedback } from './FeedbackBanner.tsx';

/** The shortest password that may protect exported secrets (the server checks it too). */
export const CONFIG_PASSWORD_MIN_LENGTH = 8;

export const CONFIG_SECTION_LABELS: Record<ConfigSection, string> = {
  notification_targets: 'Notification targets',
  docker_hosts: 'Docker hosts',
  host_aliases: 'Host aliases',
  alert_rules: 'Alert rules',
  drop_rules: 'Drop rules',
  saved_views: 'Saved views',
  maintenance_schedules: 'Maintenance schedules',
  api_tokens: 'API tokens',
  settings: 'Settings',
};

const ACTION_TONE = { add: 'success', replace: 'warning', unchanged: 'neutral', skip: 'neutral' } as const;
const ACTION_TEXT = { add: 'Add', replace: 'Replace', unchanged: 'Same', skip: 'Skip' } as const;

/** "2 to add, 1 to replace, 3 the same" for a preview row, or "Nothing to change". */
export function describeSectionPlan(add: number, replace: number, unchanged: number, skip: number): string {
  const parts: string[] = [];
  if (add) parts.push(`${add} to add`);
  if (replace) parts.push(`${replace} to replace`);
  if (unchanged) parts.push(`${unchanged} the same`);
  if (skip) parts.push(`${skip} skipped`);
  return parts.length ? parts.join(', ') : 'Nothing to change';
}

function describeResult(r: ConfigSectionResult): string {
  const parts: string[] = [];
  if (r.added) parts.push(`${r.added} added`);
  if (r.replaced) parts.push(`${r.replaced} replaced`);
  if (r.unchanged) parts.push(`${r.unchanged} the same`);
  if (r.skipped) parts.push(`${r.skipped} skipped`);
  if (r.failed.length) parts.push(`${r.failed.length} failed`);
  return parts.length ? parts.join(', ') : 'No change';
}

type ImportStep =
  | { kind: 'password' }
  | { kind: 'preview'; preview: ConfigImportPreview }
  | { kind: 'result'; sections: ConfigSectionResult[] };

export interface ConfigTransferCardProps {
  serverTimezone?: string;
  /** Called after an import changed something, so the page reads the settings again. */
  onImported: () => Promise<void> | void;
}

/**
 * Settings > Advanced: the whole configuration to a file and back. Export can add the stored secrets
 * and API tokens, encrypted with a password; import shows what each section would add or replace and
 * applies only the sections left ticked.
 */
export const ConfigTransferCard: React.FC<ConfigTransferCardProps> = ({ serverTimezone, onImported }) => {
  const [includeSecrets, setIncludeSecrets] = useState<boolean>(false);
  const [exportPassword, setExportPassword] = useState<string>('');
  const [exportConfirm, setExportConfirm] = useState<string>('');
  const [adminPassword, setAdminPassword] = useState<string>('');
  const [isExporting, setIsExporting] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [fileDoc, setFileDoc] = useState<object | null>(null);
  const [importPassword, setImportPassword] = useState<string>('');
  const [step, setStep] = useState<ImportStep | null>(null);
  const [ticked, setTicked] = useState<Set<ConfigSection>>(new Set());
  const [isBusy, setIsBusy] = useState<boolean>(false);
  const [dialogError, setDialogError] = useState<string | null>(null);

  const passwordTooShort = includeSecrets && exportPassword.length < CONFIG_PASSWORD_MIN_LENGTH;
  const passwordsDiffer = includeSecrets && exportPassword !== exportConfirm;
  const canExport = !isExporting && !passwordTooShort && !passwordsDiffer && (!includeSecrets || adminPassword !== '');

  const handleExport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canExport) return;
    setIsExporting(true);
    setFeedback(null);
    try {
      const blob = await exportConfiguration(includeSecrets, exportPassword, adminPassword);
      downloadBlob(blob, `logshed-config-${new Date().toISOString().slice(0, 10)}.json`);
      setFeedback({
        text: includeSecrets
          ? 'Configuration exported with its secrets. Keep the password: the secrets cannot be read without it.'
          : 'Configuration exported without secrets.',
        isError: false,
      });
      setExportPassword('');
      setExportConfirm('');
      setAdminPassword('');
    } catch (err: any) {
      setFeedback({ text: err.message || 'Failed to export the configuration.', isError: true });
    } finally {
      setIsExporting(false);
    }
  };

  const closeDialog = () => {
    if (isBusy) return;
    setStep(null);
    setFileDoc(null);
    setImportPassword('');
    setDialogError(null);
  };

  const loadPreview = async (doc: object, password: string) => {
    setIsBusy(true);
    setDialogError(null);
    try {
      const preview = await previewConfigurationImport(doc, password);
      setTicked(new Set(preview.sections.filter((s) => s.add + s.replace > 0).map((s) => s.name)));
      setStep({ kind: 'preview', preview });
    } catch (err: any) {
      setDialogError(err.message || 'Failed to read the file.');
    } finally {
      setIsBusy(false);
    }
  };

  const handleFileChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setFeedback(null);
    const reader = new FileReader();
    reader.onload = () => {
      let doc: any;
      try {
        doc = JSON.parse(String(reader.result));
      } catch {
        setFeedback({ text: 'The file is not JSON. Choose a file exported from Settings > Advanced.', isError: true });
        return;
      }
      if (!doc || typeof doc !== 'object' || doc.format !== 'logshed-config') {
        setFeedback({ text: 'This is not a LogShed configuration file.', isError: true });
        return;
      }
      setFileDoc(doc);
      setImportPassword('');
      setDialogError(null);
      if (doc.secrets) {
        setStep({ kind: 'password' });
      } else {
        setStep({ kind: 'preview', preview: { has_secrets: false, secrets_read: false, sections: [] } });
        void loadPreview(doc, '');
      }
    };
    reader.onerror = () => setFeedback({ text: 'The file could not be read.', isError: true });
    reader.readAsText(file);
  };

  const toggleSection = (name: ConfigSection, on: boolean) => {
    setTicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });
  };

  const handleApply = async () => {
    if (!fileDoc || ticked.size === 0) return;
    setIsBusy(true);
    setDialogError(null);
    try {
      const res = await importConfiguration(fileDoc, Array.from(ticked), importPassword);
      setStep({ kind: 'result', sections: res.sections });
      setFileDoc(null);
      setImportPassword('');
      await onImported();
    } catch (err: any) {
      setDialogError(err.message || 'Failed to import the configuration.');
    } finally {
      setIsBusy(false);
    }
  };

  const preview = step?.kind === 'preview' ? step.preview : null;

  return (
    <Card>
      <CardHeader
        icon={<FileJson />}
        title="Configuration Export & Import"
        description="Copy every saved setting and every list (sources, notification targets, alert and drop rules, saved views, maintenance schedules) to one file, or bring one in from another LogShed server. Logs and history are never included."
      />

      <FeedbackBanner feedback={feedback} onDismiss={() => setFeedback(null)} />

      <form onSubmit={handleExport} className="space-y-3">
        <Checkbox
          label="Include secrets (AI keys, notification URLs, Docker TLS keys) and API tokens, encrypted with a password"
          checked={includeSecrets}
          onChange={(e) => setIncludeSecrets(e.target.checked)}
        />
        {includeSecrets && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <TextField
              label="Admin Password"
              id="config-export-admin-password"
              type="password"
              autoComplete="current-password"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              hint="Your sign-in password, to allow reading the secrets."
              mono
            />
            <TextField
              label={`File Password (min ${CONFIG_PASSWORD_MIN_LENGTH} chars)`}
              id="config-export-password"
              type="password"
              autoComplete="new-password"
              value={exportPassword}
              onChange={(e) => setExportPassword(e.target.value)}
              hint="Needed to import the secrets. LogShed keeps no copy of it."
              mono
            />
            <TextField
              label="Confirm Password"
              id="config-export-confirm"
              type="password"
              autoComplete="new-password"
              value={exportConfirm}
              onChange={(e) => setExportConfirm(e.target.value)}
              error={exportConfirm && passwordsDiffer ? 'The passwords do not match.' : null}
              mono
            />
          </div>
        )}
        <div className="flex flex-col sm:flex-row gap-2 sm:justify-end">
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileChosen}
            accept=".json,application/json"
            className="hidden"
            data-testid="config-import-file"
          />
          <Button
            onClick={() => fileInputRef.current?.click()}
            icon={<Upload className="w-3.5 h-3.5" />}
            className="w-full sm:w-auto"
          >
            Import from File
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={!canExport}
            icon={<Download className="w-3.5 h-3.5" />}
            className="w-full sm:w-auto"
          >
            {isExporting ? 'Exporting...' : 'Export Configuration'}
          </Button>
        </div>
      </form>

      <Modal
        isOpen={step !== null}
        onClose={closeDialog}
        title={step?.kind === 'result' ? 'Configuration Imported' : 'Import Configuration'}
        icon={<Upload />}
        testId="config-import-dialog"
      >
        <div className="space-y-4">
          {dialogError && (
            <div role="alert" className="p-2.5 rounded-lg border bg-danger-950/60 border-danger-800 text-danger-300 text-xs">
              {dialogError}
            </div>
          )}

          {step?.kind === 'password' && (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (fileDoc && importPassword) void loadPreview(fileDoc, importPassword);
              }}
            >
              <p className="text-xs text-fg-soft">
                This file holds encrypted secrets and API tokens. Enter the password chosen when it was exported, or
                import it without them: notification targets that are new here are then skipped, and new Docker hosts
                with a TLS key are added turned off.
              </p>
              <TextField
                label="File Password"
                id="config-import-password"
                type="password"
                autoComplete="off"
                value={importPassword}
                onChange={(e) => setImportPassword(e.target.value)}
                mono
              />
              <div className="flex flex-col-reverse sm:flex-row gap-2 sm:justify-end">
                <Button onClick={closeDialog} disabled={isBusy}>Cancel</Button>
                <Button
                  onClick={() => {
                    setImportPassword('');
                    if (fileDoc) void loadPreview(fileDoc, '');
                  }}
                  disabled={isBusy}
                >
                  Import Without Secrets
                </Button>
                <Button type="submit" variant="primary" disabled={isBusy || !importPassword} icon={<KeyRound className="w-3.5 h-3.5" />}>
                  {isBusy ? 'Opening...' : 'Open Secrets'}
                </Button>
              </div>
            </form>
          )}

          {preview && (
            <>
              {isBusy && preview.sections.length === 0 ? (
                <p className="text-xs text-fg-muted">Reading the file...</p>
              ) : (
                <>
                  <p className="text-xs text-fg-soft">
                    {preview.logshed_version ? `Exported from LogShed ${preview.logshed_version}` : 'Exported from LogShed'}
                    {preview.exported_at ? ` on ${formatLocalTimestamp(preview.exported_at, false, serverTimezone)}` : ''}.
                    {' '}Ticked sections are imported: an item with the same name as one here replaces it, new items are added, and
                    nothing here is removed. Sections that would change nothing cannot be ticked.
                  </p>
                  {preview.has_secrets && !preview.secrets_read && (
                    <p className="text-xs text-warning-300">Read without its secrets: AI keys, notification URLs, Docker TLS keys and API tokens are not imported.</p>
                  )}
                  <ul className="space-y-2">
                    {preview.sections.map((s) => {
                      const changes = s.add + s.replace;
                      const notes = [...s.notes, ...s.items.filter((i) => i.note).map((i) => `${i.label}: ${i.note}`)];
                      return (
                        <li key={s.name} className="rounded-lg border border-line-subtle bg-canvas/40 p-2.5 space-y-1.5">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <Checkbox
                              label={CONFIG_SECTION_LABELS[s.name]}
                              checked={ticked.has(s.name)}
                              disabled={changes === 0 || isBusy}
                              onChange={(e) => toggleSection(s.name, e.target.checked)}
                            />
                            <span className="text-[11px] text-fg-muted">{describeSectionPlan(s.add, s.replace, s.unchanged, s.skip)}</span>
                          </div>
                          {s.items.length > 0 && (
                            <details className="text-[11px] text-fg-muted">
                              <summary className="cursor-pointer select-none">Show items</summary>
                              <ul className="mt-1.5 space-y-1 pl-1">
                                {s.items.map((item, idx) => (
                                  <li key={idx} className="flex items-center gap-2 min-w-0">
                                    <Badge tone={ACTION_TONE[item.action]}>{ACTION_TEXT[item.action]}</Badge>
                                    <span className="font-mono text-fg-soft truncate" title={item.label}>{item.label}</span>
                                  </li>
                                ))}
                              </ul>
                            </details>
                          )}
                          {notes.length > 0 && (
                            <ul className="text-[11px] text-warning-300 space-y-0.5">
                              {notes.map((n, idx) => <li key={idx}>{n}</li>)}
                            </ul>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                  <div className="flex flex-col-reverse sm:flex-row gap-2 sm:justify-end">
                    <Button onClick={closeDialog} disabled={isBusy}>Cancel</Button>
                    <Button
                      variant="primary"
                      onClick={handleApply}
                      disabled={isBusy || ticked.size === 0}
                      icon={<Upload className="w-3.5 h-3.5" />}
                    >
                      {isBusy ? 'Importing...' : `Import ${ticked.size} ${ticked.size === 1 ? 'Section' : 'Sections'}`}
                    </Button>
                  </div>
                </>
              )}
            </>
          )}

          {step?.kind === 'result' && (
            <>
              <ul className="space-y-2">
                {step.sections.map((r) => (
                  <li key={r.name} className="rounded-lg border border-line-subtle bg-canvas/40 p-2.5 space-y-1">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                      <span className="font-medium text-fg-soft">{CONFIG_SECTION_LABELS[r.name]}</span>
                      <span className={r.failed.length ? 'text-danger-300' : 'text-fg-muted'}>{describeResult(r)}</span>
                    </div>
                    {r.failed.length > 0 && (
                      <ul className="text-[11px] text-danger-300 space-y-0.5">
                        {r.failed.map((f, idx) => <li key={idx}>{f.label}: {f.error}</li>)}
                      </ul>
                    )}
                    {r.notes.length > 0 && (
                      <ul className="text-[11px] text-warning-300 space-y-0.5">
                        {r.notes.map((n, idx) => <li key={idx}>{n}</li>)}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
              <div className="flex justify-end">
                <Button variant="primary" onClick={closeDialog}>Close</Button>
              </div>
            </>
          )}
        </div>
      </Modal>
    </Card>
  );
};
