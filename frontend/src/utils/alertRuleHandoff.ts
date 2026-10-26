/**
 * One-shot, in-memory hand-off from the log detail drawer to Rules & History: "Create Alert Rule"
 * stores the log's app and message here, and AlertsPanel takes it once to open a prefilled dialog.
 * Kept out of the URL and out of history state because messages are long and may hold sensitive text.
 */
export interface AlertRuleHandoff {
  appName?: string;
  /** Log message, already cleaned for display. */
  message?: string;
}

let pendingHandoff: AlertRuleHandoff | null = null;

export function setAlertRuleHandoff(handoff: AlertRuleHandoff): void {
  pendingHandoff = handoff;
}

/** Returns the pending hand-off and clears it, so it opens the dialog at most once. */
export function takeAlertRuleHandoff(): AlertRuleHandoff | null {
  const handoff = pendingHandoff;
  pendingHandoff = null;
  return handoff;
}
