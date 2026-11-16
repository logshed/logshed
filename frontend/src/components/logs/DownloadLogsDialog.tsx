import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Download, RefreshCw } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';
import { Button } from '../common/Button.tsx';
import { Checkbox, Field, SELECT_FIELD_CLASS } from '../common/Field.tsx';
import { SelectDropdown, SelectOption } from '../common/SelectDropdown.tsx';
import { downloadLogs, LOG_DOWNLOAD_CAP, LogDownloadFormat } from '../../api/logs.ts';
import { downloadBlob } from '../../utils/formatters.ts';
import { LogFilterParams } from '../../types.ts';

const FORMAT_OPTIONS: SelectOption[] = [
  { value: 'csv', label: 'CSV (spreadsheets)' },
  { value: 'ndjson', label: 'NDJSON (one JSON object per line)' },
];

export type DownloadScope = 'search' | 'selected';

interface DownloadLogsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  /** The Console's search, downloaded when no rows are ticked or "Current search" is chosen. */
  filters: LogFilterParams;
  /** The ticked rows' ids. */
  selectedIds: number[];
  /** Which logs the dialog offers first: the ticked rows when opened from the selection bar. */
  initialScope: DownloadScope;
}

/**
 * Download the Console's logs as CSV or NDJSON: the current search (the newest 100,000) or the
 * ticked rows, exact or with secrets masked. Shows how many the file holds, and says so when the
 * search matched more than the cap.
 */
export const DownloadLogsDialog: React.FC<DownloadLogsDialogProps> = ({
  isOpen,
  onClose,
  filters,
  selectedIds,
  initialScope,
}) => {
  const [format, setFormat] = useState<LogDownloadFormat>('csv');
  const [maskSecrets, setMaskSecrets] = useState(false);
  const [scope, setScope] = useState<DownloadScope>(initialScope);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cappedRows, setCappedRows] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Each opening starts from the scope it was opened for; format and masking stay as last chosen
  useEffect(() => {
    if (isOpen) {
      setScope(selectedIds.length > 0 ? initialScope : 'search');
      setError(null);
      setCappedRows(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset on opening only, not as rows are ticked
  }, [isOpen]);

  // Closing the dialog (or leaving the page) stops a download in progress
  useEffect(() => {
    if (!isOpen) abortRef.current?.abort();
  }, [isOpen]);
  useEffect(() => () => abortRef.current?.abort(), []);

  const handleClose = () => {
    abortRef.current?.abort();
    onClose();
  };

  const handleDownload = async () => {
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError(null);
    setCappedRows(null);
    try {
      const result = await downloadLogs(
        scope === 'selected' ? { format, maskSecrets, logIds: selectedIds } : { format, maskSecrets, filters },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      downloadBlob(result.blob, result.filename);
      if (result.capped) setCappedRows(result.rows);
      else onClose();
    } catch (err: any) {
      if (controller.signal.aborted || err?.name === 'AbortError') return;
      setError(
        err?.status
          ? err.message || 'The download failed.'
          : 'The download stopped before the end (the search ran out of time or the connection dropped). Narrow the search and try again.',
      );
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setBusy(false);
      }
    }
  };

  const scopeOptions: SelectOption[] = [
    { value: 'search', label: 'Current search (newest first)' },
    { value: 'selected', label: `Ticked rows (${selectedIds.length.toLocaleString()})` },
  ];

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title="Download Logs" icon={<Download />} maxWidth="max-w-md" testId="download-logs-dialog">
      <div className="space-y-4 text-xs">
        {selectedIds.length > 0 && (
          <Field label="Logs" id="download-scope">
            {({ id, labelId }) => (
              <SelectDropdown
                variant="panel"
                fieldClassName={SELECT_FIELD_CLASS}
                id={id}
                labelledBy={labelId}
                label="Logs"
                options={scopeOptions}
                value={scope}
                onChange={(v) => setScope(v as DownloadScope)}
                disabled={busy}
              />
            )}
          </Field>
        )}
        <Field label="Format" id="download-format">
          {({ id, labelId }) => (
            <SelectDropdown
              variant="panel"
              fieldClassName={SELECT_FIELD_CLASS}
              id={id}
              labelledBy={labelId}
              label="Format"
              options={FORMAT_OPTIONS}
              value={format}
              onChange={(v) => setFormat(v as LogDownloadFormat)}
              disabled={busy}
            />
          )}
        </Field>
        <div className="space-y-1">
          <Checkbox
            label="Mask secrets"
            checked={maskSecrets}
            onChange={(e) => setMaskSecrets(e.target.checked)}
            disabled={busy}
            aria-describedby="download-mask-hint"
          />
          <p id="download-mask-hint" className="text-[11px] text-fg-muted">
            Replaces passwords, tokens and keys in each message with [REDACTED], as LogShed does before sending logs to
            an AI provider. Off: the logs are exactly as stored.
          </p>
        </div>
        <p className="text-[11px] text-fg-muted">
          {scope === 'selected'
            ? 'The ticked rows, newest first, with every field and the raw text.'
            : `At most the newest ${LOG_DOWNLOAD_CAP.toLocaleString()} logs of the search, with every field and the raw text.`}
          {' Times are in UTC.'}
          {format === 'csv' && ' In CSV, a value that starts with =, +, -, @, a tab or a carriage return gets a leading apostrophe, so a spreadsheet does not run it as a formula.'}
        </p>

        {cappedRows !== null && (
          <p role="status" className="flex items-start gap-2 rounded-sm border border-warning-800/70 bg-warning-950/50 p-2.5 text-warning-300">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>
              The search matched more than {LOG_DOWNLOAD_CAP.toLocaleString()} logs, so the file holds the newest{' '}
              {cappedRows.toLocaleString()}. To get older ones, set an end time (To) before the oldest log in this file and
              download again.
            </span>
          </p>
        )}
        {error && (
          <p role="alert" className="flex items-start gap-2 rounded-sm border border-danger-800/60 bg-danger-950/40 p-2.5 text-danger-300">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </p>
        )}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-line-subtle">
          <Button variant="secondary" onClick={handleClose}>
            {cappedRows !== null ? 'Close' : 'Cancel'}
          </Button>
          <Button
            variant="primary"
            onClick={handleDownload}
            disabled={busy}
            icon={busy ? <RefreshCw className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Download className="w-3.5 h-3.5" aria-hidden="true" />}
          >
            {busy ? 'Preparing...' : 'Download'}
          </Button>
        </div>
      </div>
    </Modal>
  );
};
