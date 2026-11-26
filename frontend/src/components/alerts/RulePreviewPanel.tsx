import { useMemo, useState } from 'react';
import { History } from 'lucide-react';
import { RulePreviewResponse } from '../../types.ts';
import { Button } from '../common/Button.tsx';
import { formatLocalTimestamp, formatStreamTimestamp, getTodayKey } from '../../utils/formatters.ts';
import { useServerTimezone } from '../../utils/hooks.ts';

/** A span of time as "45 min" or "3 h 20 min". */
export function formatSpan(ms: number): string {
  const minutes = Math.max(1, Math.round(ms / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/**
 * The sentence that sums up a check, e.g. "Would have matched 12 of 3,400 logs from the last 24
 * hours." scope names the logs the server read when it read only some (an alert rule's severity
 * filter), e.g. "Warning (4) and more severe".
 */
export function previewSummary(
  result: RulePreviewResponse,
  verb: 'matched' | 'dropped',
  timeZone?: string,
  now: number = Date.now(),
  scope?: string
): string {
  const logs = scope ? `logs (${scope})` : 'logs';
  if (result.checked === 0) return `No stored ${logs} from the last 24 hours to check.`;
  const counted = `Would have ${verb} ${result.matched.toLocaleString()}`;
  if (result.complete) {
    return `${counted} of ${result.checked.toLocaleString()} ${logs} from the last 24 hours.`;
  }
  const back = result.oldest_checked ? Date.parse(result.oldest_checked) : NaN;
  const reach = Number.isNaN(back)
    ? ''
    : `, back to ${formatLocalTimestamp(result.oldest_checked, false, timeZone)} (the last ${formatSpan(now - back)})`;
  const limit =
    result.stopped_by === 'time'
      ? 'A check stops after 5 seconds.'
      : `A check reads at most ${result.row_cap.toLocaleString()} logs.`;
  return `${counted} of the newest ${result.checked.toLocaleString()} ${logs}${reach}. ${limit}`;
}

interface RulePreviewPanelProps<T> {
  /** The rule as the dialog holds it now; a result is shown only while the rule is unchanged. */
  request: T;
  run: (request: T) => Promise<RulePreviewResponse>;
  verb: 'matched' | 'dropped';
  /** What the count leaves out, shown under the result. */
  note: string;
  /** The logs the server reads, when not all of them (previewSummary's scope). */
  scope?: string;
  disabled?: boolean;
}

/**
 * "Check last 24h": counts the stored logs of the last 24 hours the rule would have matched,
 * with the newest few, using the rule's own matcher on the server.
 */
export function RulePreviewPanel<T>({ request, run, verb, note, scope, disabled = false }: RulePreviewPanelProps<T>) {
  const timeZone = useServerTimezone();
  const key = JSON.stringify(request);
  const [outcome, setOutcome] = useState<{ key: string; result?: RulePreviewResponse; error?: string; at: number } | null>(null);
  const [isChecking, setIsChecking] = useState(false);
  const shown = outcome && outcome.key === key ? outcome : null;
  const todayKey = useMemo(() => getTodayKey(timeZone), [timeZone]);

  const handleCheck = async () => {
    if (isChecking) return;
    setIsChecking(true);
    try {
      const result = await run(request);
      setOutcome({ key, result, error: result.error || undefined, at: Date.now() });
    } catch (err: any) {
      setOutcome({ key, error: err?.message || 'The check failed.', at: Date.now() });
    } finally {
      setIsChecking(false);
    }
  };

  const result = shown && !shown.error ? shown.result : undefined;

  return (
    <div className="bg-canvas border border-line-subtle rounded-lg p-3 space-y-2" data-testid="rule-preview">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-fg text-xs">Stored logs</span>
        <Button
          size="sm"
          icon={<History className="w-3 h-3 text-accent-400" aria-hidden="true" />}
          onClick={handleCheck}
          // Not disabled while checking: a disabled button drops keyboard focus out of the dialog
          disabled={disabled}
          aria-disabled={isChecking || undefined}
          aria-busy={isChecking}
        >
          {isChecking ? 'Checking...' : 'Check last 24h'}
        </Button>
      </div>
      <div role="status" className="space-y-2">
        {!shown && (
          <p className="text-[11px] text-fg-muted">
            {verb === 'dropped'
              ? 'See how many logs stored in the last 24 hours this rule would have dropped.'
              : 'See how many logs stored in the last 24 hours this rule would have matched.'}
          </p>
        )}
        {shown?.error && <p className="text-[11px] text-danger-400">{shown.error}</p>}
        {result && (
          <>
            <p className="text-xs text-fg" data-testid="rule-preview-summary">
              {previewSummary(result, verb, timeZone, shown!.at, scope)}
            </p>
            {result.pattern_timeouts > 0 && (
              <p className="text-[11px] text-warning-300">
                The pattern timed out on {result.pattern_timeouts.toLocaleString()}{' '}
                {result.pattern_timeouts === 1 ? 'log' : 'logs'}, counted as no match. A saved rule whose pattern keeps
                timing out is switched off.
              </p>
            )}
            {result.examples.length > 0 && (
              <ul className="space-y-1" aria-label={verb === 'dropped' ? 'Newest logs it would have dropped' : 'Newest matching logs'}>
                {result.examples.map((log) => (
                  <li key={log.id} className="text-[11px] font-mono text-fg-soft truncate" title={log.message}>
                    <span className="text-fg-muted">{formatStreamTimestamp(log.timestamp, log.received_at, timeZone, todayKey)}</span>{' '}
                    <span className="text-fg-muted">{log.source_alias || log.source_ip}</span>{' '}
                    {log.app_name && <span className="text-fg-muted">{log.app_name}:</span>} {log.message}
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[11px] text-fg-muted">{note}</p>
          </>
        )}
      </div>
    </div>
  );
}
