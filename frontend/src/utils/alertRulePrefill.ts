import type { AlertRulePrefill } from '../components/alerts/AlertRuleModal.tsx';
import type { LogEntry } from '../types.ts';
import { cleanLogMessageForDisplay } from './formatters.ts';

/**
 * The new alert rule the log drawer's Create Alert Rule starts from: named after the log's app (or
 * its host), filtered to its host (its name, or its address when it has none) and to its app when
 * that is a known app, with the message as the drawer shows it to select a pattern from.
 */
export function alertRulePrefillFromLog(log: LogEntry, knownApps: string[]): AlertRulePrefill {
  const appName = log.app_name?.trim() || '';
  const hostName = (log.source_alias || log.source_ip || '').trim();
  return {
    name: appName ? `Alert on ${appName}` : hostName ? `Alert on ${hostName}` : '',
    filterApps: appName && knownApps.includes(appName) ? [appName] : [],
    filterHosts: hostName ? [hostName] : [],
    referenceMessage: cleanLogMessageForDisplay(log.message),
  };
}
