import { DockerEndpoint, LogEntry } from '../types.ts';
import { cleanLogMessageForDisplay, formatStreamTimestamp } from './formatters.ts';

/** A log as the Console list shows it: host alias applied, timestamp and message formatted once. */
export interface ProcessedLogEntry extends LogEntry {
  /** source_alias as stored, before prepareLogEntry shows a syslog host alias in its place. */
  storedSourceAlias?: string;
  formattedTimestamp: string;
  cleanedMessage: string;
}


/**
 * Every source_alias that marks a row as Docker output: the configured Docker hosts' aliases and
 * the former ones of deleted or renamed hosts (their old container logs keep them).
 */
export function buildDockerAliasSet(endpoints: DockerEndpoint[], formerAliases: string[]): Set<string> {
  const s = new Set<string>(formerAliases);
  endpoints.forEach((ep) => {
    if (ep.source_alias) s.add(ep.source_alias);
  });
  return s;
}

/**
 * Give rows whose stored alias turns out to be a Docker alias that alias back. Rows prepared
 * before the Docker hosts and former Docker aliases were loaded may show the syslog alias of
 * their address instead. Returns the same array when nothing changes.
 */
export function restoreDockerAliases(
  logs: ProcessedLogEntry[],
  dockerAliases: Set<string>
): ProcessedLogEntry[] {
  let changed = false;
  const next = logs.map((log) => {
    const stored = log.storedSourceAlias;
    if (!stored || log.source_alias === stored || !dockerAliases.has(stored)) return log;
    changed = true;
    return { ...log, source_alias: stored };
  });
  return changed ? next : logs;
}

/**
 * Prepare the rows already shown again with a newly loaded alias map and Docker aliases, for rows
 * that arrived before the alias list did. Returns the same array when no row's host changes.
 */
export function reapplyAliases(
  logs: ProcessedLogEntry[],
  aliases: Record<string, string>,
  dockerAliases: Set<string>
): ProcessedLogEntry[] {
  let changed = false;
  const next = logs.map((log) => {
    const prepared = prepareLogEntry(log, aliases, undefined, dockerAliases);
    if (prepared.source_alias === log.source_alias) return log;
    changed = true;
    return prepared;
  });
  return changed ? next : logs;
}

export function prepareLogEntry(
  entry: LogEntry | ProcessedLogEntry,
  aliases?: Record<string, string>,
  timeZone?: string,
  dockerAliases?: Set<string>,
  todayKey?: string,
): ProcessedLogEntry {
  const existing = entry as Partial<ProcessedLogEntry>;
  const storedSourceAlias = existing.storedSourceAlias ?? entry.source_alias;
  const isDocker =
    entry.source_ip === 'docker' ||
    Boolean(
      dockerAliases && storedSourceAlias && dockerAliases.has(storedSourceAlias)
    );

  const canonical =
    !isDocker &&
    aliases &&
    ((storedSourceAlias && aliases[storedSourceAlias]) ||
      (entry.source_ip && aliases[entry.source_ip]));
  const source_alias = canonical || storedSourceAlias;

  return {
    ...entry,
    source_alias,
    storedSourceAlias,
    formattedTimestamp:
      existing.formattedTimestamp ??
      formatStreamTimestamp(entry.timestamp, entry.received_at, timeZone, todayKey),
    cleanedMessage: existing.cleanedMessage ?? cleanLogMessageForDisplay(entry.message),
  };
}

/** Recomputes every row's display timestamp, for a new time zone or after midnight. */
export function reformatTimestamps(
  logs: ProcessedLogEntry[],
  timeZone: string | undefined,
  todayKey: string,
): ProcessedLogEntry[] {
  return logs.map((l) => ({
    ...l,
    formattedTimestamp: formatStreamTimestamp(l.timestamp, l.received_at, timeZone, todayKey),
  }));
}

/** Newest first, as the list shows logs: by timestamp, then by id. */
function newestFirst(a: LogEntry, b: LogEntry): number {
  const cmp = b.timestamp.localeCompare(a.timestamp);
  return cmp !== 0 ? cmp : b.id - a.id;
}

/**
 * Adds the entries that are not in the list yet, prepared, and puts the list back in time order,
 * for logs fetched around one (the drawer's context logs). Returns the same array when all are there.
 */
export function mergeRowsInTimeOrder(
  logs: ProcessedLogEntry[],
  entries: LogEntry[],
  prepare: (entries: LogEntry[]) => ProcessedLogEntry[],
): ProcessedLogEntry[] {
  const existingIds = new Set(logs.map((l) => l.id));
  const missing = prepare(entries.filter((l) => !existingIds.has(l.id)));
  if (missing.length === 0) return logs;
  return [...missing, ...logs].sort(newestFirst);
}
