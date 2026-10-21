/**
 * Log message formatting and sanitization utilities.
 */

// Matches ANSI CSI sequences (e.g. \x1b[32m, \x1b[0m, \x1b[1;31m), OSC sequences, and two-character ESC codes
const ANSI_REGEX = /\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\)|[@-Z\\-_])/g;

// Matches non-printable ASCII control characters (ASCII 0-8, 11-12, 14-31, 127), preserving \t (9), \n (10), \r (13)
const CONTROL_CHARS_REGEX = /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g;

/**
 * Strips ANSI escape sequences and non-printable control characters from text.
 * Prevents mobile browser glyph replacement artifacts (like square boxes with an X: ☒)
 * and terminal color code artifacts (like [32minfo[39m).
 */
export function stripAnsi(text: string | null | undefined): string {
  if (!text) return '';
  return text
    .replace(ANSI_REGEX, '')
    .replace(CONTROL_CHARS_REGEX, '');
}

const LEVEL_WORDS =
  'emerg|emergency|alert|crit|critical|fatal|panic|err|error|warn|warning|notice|log|info|informational|debug|trace|verbose';

/**
 * Regex patterns for leading timestamps in log messages:
 * - ISO 8601 / RFC 3339: [2026-09-12T05:03:48+01:00] or 2026-09-12T05:33:55.369Z
 * - With named timezone: [2026-09-12 07:48:57 UTC] or 2026-09-12 08:50:39.344 BST
 * - Standard datetime: 2026-09-12 06:35:02
 * - With comma millis (Python logging): [2026-09-12 08:47:00,011]
 * - Date with slashes: 2026/09/12 07:43:00
 * - BSD syslog timestamp: Sep 12 06:35:02
 */
const LEADING_TIMESTAMP_REGEX =
  /^(?:\[\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+(?:UTC|GMT|CET|EET|WET|MSK|[A-Z]{1,2}[SD]T)(?:[+-]\d{1,4})?|Z|[+-]\d{2}:?\d{2})?\]|\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+(?:UTC|GMT|CET|EET|WET|MSK|[A-Z]{1,2}[SD]T)(?:[+-]\d{1,4})?|Z|[+-]\d{2}:?\d{2})?|[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})\s*/;

const SUBSYSTEM_WITH_LEVEL_REGEX = new RegExp(
  `^(\\[[^\\]]+\\])\\s+(?:${LEVEL_WORDS})\\s*:?\\s+`,
  'i'
);

const BRACKET_LEVEL_REGEX = new RegExp(`^\\[(?:${LEVEL_WORDS})\\]\\s*:?\\s*`, 'i');

const WORD_LEVEL_REGEX =
  /^(?:(?:emerg|emergency|alert|crit|critical|fatal|panic|err|error|warn|warning|notice|info|informational|debug|trace|verbose)\s*:?\s+|log:\s+)/i;

/**
 * Additional prefix matchers:
 * - Valkey / Redis server line: "1:M 02 Oct 2026 11:47:57.745 # " or "1:S ... * "
 * - Application-prefixed pipe: "[maintainerr] | 03/10/2026 16:00:33 "
 * - Day-first slash date: "03/10/2026 16:00:33 "
 */
const VALKEY_PREFIX_REGEX = /^\d+:[a-zA-Z]\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s+[#*.-]\s*/;

const APP_PIPE_DATE_REGEX = /^\[[^\]]+\]\s*\|\s*\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*/;

const DMY_SLASH_TIMESTAMP_REGEX = /^\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*/;

const LOGFMT_MSG_REGEX = /^(?:time|ts)=["']?[^"'\s]+["']?\s+(?:.*?\b)?msg=(?:"([^"]*)"|'([^']*)'|(\S+))/;

/**
 * Trims redundant leading timestamps and repeated severity prefixes from message text
 * for clean display in the live log stream table rows.
 * Preserves the original message content in full if no redundant prefix matches.
 */
export function cleanLogMessageForDisplay(text: string | null | undefined): string {
  if (!text) return '';
  const stripped = stripAnsi(text);
  let clean = stripped.trim();

  // 1. Check for logfmt format with msg="..."
  const logfmtMatch = clean.match(LOGFMT_MSG_REGEX);
  if (logfmtMatch) {
    const extractedMsg = logfmtMatch[1] ?? logfmtMatch[2] ?? logfmtMatch[3];
    if (extractedMsg) {
      return extractedMsg;
    }
  }

  // 2. Strip Valkey / Redis prefix (<pid>:<role> <date> <time> <level_char>)
  clean = clean.replace(VALKEY_PREFIX_REGEX, '');

  // 3. Strip [app] | DD/MM/YYYY HH:MM:SS prefix
  clean = clean.replace(APP_PIPE_DATE_REGEX, '');

  // 4. Strip leading redundant timestamps (including DD/MM/YYYY)
  clean = clean.replace(LEADING_TIMESTAMP_REGEX, '');
  clean = clean.replace(DMY_SLASH_TIMESTAMP_REGEX, '');

  // 5. If preceded by a bracketed subsystem tag like [MONITOR] before the level, preserve the subsystem:
  clean = clean.replace(SUBSYSTEM_WITH_LEVEL_REGEX, '$1 ');

  // 6. Match leading bracketed level: [error], [warn], [info], etc.
  clean = clean.replace(BRACKET_LEVEL_REGEX, '');

  // 7. Match leading unbracketed level: "WARN: ...", "INFO   ..."
  clean = clean.replace(WORD_LEVEL_REGEX, '');

  const result = clean.trim();
  return result.length > 0 ? result : stripped;
}

/**
 * Formats a UTC ISO datetime string into a local 'YYYY-MM-DDTHH:mm' string
 * suitable for the value attribute of an HTML5 <input type="datetime-local" />.
 */
export function toLocalDatetimeInputString(isoString?: string | null): string {
  if (!isoString) return '';
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return '';
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

/**
 * Converts a local 'YYYY-MM-DDTHH:mm' string from an HTML5 <input type="datetime-local" />
 * into a UTC ISO datetime string suitable for backend API log filtering.
 */
export function fromLocalDatetimeInputString(localString?: string | null): string | undefined {
  if (!localString || !localString.trim()) return undefined;
  const [datePart, timePart] = localString.trim().split('T');
  if (!datePart || !timePart) return undefined;
  const [year, month, day] = datePart.split('-').map(Number);
  const timePieces = timePart.split(':').map(Number);
  const hours = timePieces[0];
  const minutes = timePieces[1];
  if (
    isNaN(year) || isNaN(month) || isNaN(day) ||
    isNaN(hours) || isNaN(minutes)
  ) {
    return undefined;
  }
  const d = new Date(year, month - 1, day, hours, minutes);
  if (isNaN(d.getTime())) return undefined;
  return d.toISOString();
}

/**
 * Prepares a backend timestamp for Date parsing: a value without a zone suffix is treated as UTC.
 */
export function cleanIsoString(ts: string): string {
  let parseable = ts.trim();
  if (!parseable.endsWith('Z') && !/[+-]\d{2}(:\d{2})?$/.test(parseable)) {
    parseable = parseable.replace(' ', 'T') + 'Z';
  }
  return parseable;
}

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

// One formatter per time zone. Building an Intl.DateTimeFormat is far more costly than using one,
// and the live stream formats every incoming row. null marks a time zone the runtime rejected.
const dateTimeFormatterCache = new Map<string, Intl.DateTimeFormat | null>();

function getDateTimeFormatter(timeZone: string): Intl.DateTimeFormat | null {
  const cached = dateTimeFormatterCache.get(timeZone);
  if (cached !== undefined) return cached;
  let formatter: Intl.DateTimeFormat | null = null;
  try {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    });
  } catch {
    formatter = null;
  }
  dateTimeFormatterCache.set(timeZone, formatter);
  return formatter;
}

interface DateParts {
  year: number;
  /** 1-based month */
  month: number;
  day: number;
  hours: string;
  minutes: string;
  seconds: string;
}

/** Calendar and clock fields of a date in the given time zone, or in browser time when none (or an invalid one) is given. */
function getDateParts(date: Date, timeZone?: string): DateParts {
  const formatter = timeZone ? getDateTimeFormatter(timeZone) : null;
  if (formatter) {
    const parts: DateParts = { year: 0, month: 0, day: 0, hours: '00', minutes: '00', seconds: '00' };
    for (const p of formatter.formatToParts(date)) {
      if (p.type === 'year') parts.year = parseInt(p.value, 10);
      else if (p.type === 'month') parts.month = parseInt(p.value, 10);
      else if (p.type === 'day') parts.day = parseInt(p.value, 10);
      else if (p.type === 'hour') parts.hours = (p.value === '24' ? '00' : p.value).padStart(2, '0');
      else if (p.type === 'minute') parts.minutes = p.value.padStart(2, '0');
      else if (p.type === 'second') parts.seconds = p.value.padStart(2, '0');
    }
    return parts;
  }
  return {
    year: date.getFullYear(),
    month: date.getMonth() + 1,
    day: date.getDate(),
    hours: String(date.getHours()).padStart(2, '0'),
    minutes: String(date.getMinutes()).padStart(2, '0'),
    seconds: String(date.getSeconds()).padStart(2, '0'),
  };
}

function dayKey(parts: DateParts): string {
  return `${parts.year}-${String(parts.month).padStart(2, '0')}-${String(parts.day).padStart(2, '0')}`;
}

/**
 * The current calendar day ('YYYY-MM-DD') in the given time zone (browser time when none is given).
 * The live stream computes it once per batch and passes it to formatStreamTimestamp.
 */
export function getTodayKey(timeZone?: string, now: Date = new Date()): string {
  return dayKey(getDateParts(now, timeZone));
}

/**
 * Formats a log timestamp for a live stream row: 'HH:mm:ss.SSS' for today, 'DD MMM HH:mm:ss' otherwise.
 * A timestamp more than 60s ahead of received_at (legacy RFC 3164 rows) falls back to received_at.
 * Pass todayKey (from getTodayKey) when formatting many rows so "today" is computed once.
 */
export function formatStreamTimestamp(
  ts: string,
  fallbackTs?: string,
  timeZone?: string,
  todayKey?: string,
): string {
  if (!ts && !fallbackTs) return '';
  try {
    let target = ts || fallbackTs || '';
    let d = new Date(cleanIsoString(target));
    if (ts && fallbackTs) {
      const dFb = new Date(cleanIsoString(fallbackTs));
      if (!isNaN(d.getTime()) && !isNaN(dFb.getTime()) && d.getTime() - dFb.getTime() > 60000) {
        target = fallbackTs;
        d = dFb;
      }
    }
    if (isNaN(d.getTime())) {
      return target;
    }

    const parts = getDateParts(d, timeZone);
    const today = todayKey ?? getTodayKey(timeZone);
    if (dayKey(parts) === today) {
      const millis = String(d.getMilliseconds()).padStart(3, '0');
      return `${parts.hours}:${parts.minutes}:${parts.seconds}.${millis}`;
    }
    const day = String(parts.day).padStart(2, '0');
    return `${day} ${MONTH_NAMES[parts.month - 1]} ${parts.hours}:${parts.minutes}:${parts.seconds}`;
  } catch {
    return ts || fallbackTs || '';
  }
}

/**
 * Formats a UTC ISO datetime string into local or configured timezone representation: 'YYYY-MM-DD HH:mm:ss' (or 'YYYY-MM-DD HH:mm' if includeSeconds is false).
 */
export function formatLocalTimestamp(
  isoString?: string | null,
  includeSeconds: boolean = true,
  timeZone?: string
): string {
  if (!isoString) return '';
  const clean = cleanIsoString(isoString);
  const date = new Date(clean);
  if (isNaN(date.getTime())) return isoString;

  const parts = getDateParts(date, timeZone);
  const base = `${dayKey(parts)} ${parts.hours}:${parts.minutes}`;
  return includeSeconds ? `${base}:${parts.seconds}` : base;
}

/**
 * Formats a timestamp into a full representation with milliseconds: 'YYYY-MM-DD HH:mm:ss.SSS'
 * respecting an optional configured timezone.
 */
export function formatFullTimestamp(
  isoString?: string | null,
  timeZone?: string
): string {
  if (!isoString) return '';
  const clean = cleanIsoString(isoString);
  const date = new Date(clean);
  if (isNaN(date.getTime())) return isoString;

  const millis = String(date.getMilliseconds()).padStart(3, '0');
  const base = formatLocalTimestamp(clean, true, timeZone);
  return `${base}.${millis}`;
}

/**
 * The UTC offset in force at a timestamp, as 'UTC+01:00', in the given time zone (browser time
 * when none, or an invalid one, is given). Worked out per timestamp, so a log from before a
 * daylight saving change shows the offset it had then. Returns '' for an unparseable value.
 */
export function formatUtcOffset(isoString?: string | null, timeZone?: string): string {
  if (!isoString) return '';
  const date = new Date(cleanIsoString(isoString));
  if (isNaN(date.getTime())) return '';

  // The zone's wall-clock fields read as if they were UTC, minus the real instant, is the offset
  const parts = getDateParts(date, timeZone);
  const wallMs = Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    Number(parts.hours),
    Number(parts.minutes),
    Number(parts.seconds),
  );
  const instantMs = Math.floor(date.getTime() / 1000) * 1000;
  const offsetMinutes = Math.round((wallMs - instantMs) / 60000);
  const sign = offsetMinutes >= 0 ? '+' : '-';
  const abs = Math.abs(offsetMinutes);
  return `UTC${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`;
}

/**
 * Slugifies text into an alphanumeric kebab-cased string suitable for filenames.
 */
export function slugify(text: string): string {
  return text
    .toString()
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '-')
    .replace(/[^\w-]+/g, '')
    .replace(/--+/g, '-')
    .replace(/^-+/, '')
    .replace(/-+$/, '') || 'rule';
}

/**
 * Initiates a browser-native file download from a Blob.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Formats a maintenance window end timestamp into a clean, human-readable time string.
 */
export function formatMaintenanceTime(isoString?: string | null): string {
  if (!isoString) return '';
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return isoString;
  const now = new Date();
  const isSameDay =
    d.getDate() === now.getDate() &&
    d.getMonth() === now.getMonth() &&
    d.getFullYear() === now.getFullYear();
  const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (isSameDay) {
    return timeStr;
  }
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${timeStr}`;
}

/**
 * Formats a byte quantity into a human-readable size string (B, KB, MB, GB, TB, PB).
 * Handles zero and negative values defensively without returning "NaN undefined".
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 B';
  }
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const unitIndex = Math.min(Math.max(0, i), sizes.length - 1);
  return `${parseFloat((bytes / Math.pow(k, unitIndex)).toFixed(2))} ${sizes[unitIndex]}`;
}

/**
 * Formats an ISO datetime string into human-readable date (e.g. "Oct 9, 2026").
 * Returns "Never" when input is empty or null.
 */
export function formatDate(isoString?: string | null): string {
  if (!isoString) return 'Never';
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return String(isoString);
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}



