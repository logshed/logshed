import { describe, it, expect, vi } from 'vitest';
import {
  stripAnsi,
  cleanLogMessageForDisplay,
  toLocalDatetimeInputString,
  fromLocalDatetimeInputString,
  slugify,
  downloadBlob,
  formatMaintenanceTime,
  formatLocalTimestamp,
} from '../utils/formatters.ts';

describe('stripAnsi', () => {
  it('handles null, undefined, and empty string gracefully', () => {
    expect(stripAnsi(null)).toBe('');
    expect(stripAnsi(undefined)).toBe('');
    expect(stripAnsi('')).toBe('');
  });

  it('preserves clean text without any escape characters', () => {
    const text = '2026-09-11T19:40:00.041Z [info][Plex Scan]: Beginning to process';
    expect(stripAnsi(text)).toBe(text);
  });

  it('strips Overseerr/Jellyseerr style ANSI color codes like [\\x1b[32minfo\\x1b[39m]', () => {
    const input = '2026-09-11T19:40:00.041Z [\x1b[32minfo\x1b[39m][Plex Scan]: Beginning to process';
    const expected = '2026-09-11T19:40:00.041Z [info][Plex Scan]: Beginning to process';
    expect(stripAnsi(input)).toBe(expected);
  });

  it('strips multiline and complex ANSI color codes and resets', () => {
    const input = '\x1b[1;31mERROR:\x1b[0m Database connection lost\n\x1b[33mWARN:\x1b[0m Retrying in 5s';
    const expected = 'ERROR: Database connection lost\nWARN: Retrying in 5s';
    expect(stripAnsi(input)).toBe(expected);
  });

  it('strips stray non-printable control characters that trigger mobile glyph replacement', () => {
    // ASCII \x1b alone without trailing CSI code, plus other non-printable chars
    const input = 'Log with stray escape \x1b and bell \x07 and null \x00 character';
    expect(stripAnsi(input)).toBe('Log with stray escape  and bell  and null  character');
  });

  it('preserves tabs and newlines', () => {
    const input = 'Line 1\n\tIndented with tab\r\nLine 2';
    expect(stripAnsi(input)).toBe('Line 1\n\tIndented with tab\r\nLine 2');
  });
});

describe('cleanLogMessageForDisplay', () => {
  it('handles null, undefined, and empty string gracefully', () => {
    expect(cleanLogMessageForDisplay(null)).toBe('');
    expect(cleanLogMessageForDisplay(undefined)).toBe('');
    expect(cleanLogMessageForDisplay('')).toBe('');
  });

  it('trims leading ISO timestamp and level while preserving message body (Example 1)', () => {
    const input = '2026-09-12 06:35:02  INFO      All scopes processed';
    expect(cleanLogMessageForDisplay(input)).toBe('All scopes processed');
  });

  it('trims leading timestamp and level while preserving bracketed subsystem tag (Example 2)', () => {
    const input =
      "2026-09-12T05:03:48+01:00 [MONITOR] WARN: Monitor #16 'SABnzbd': Pending: connect EHOSTUNREACH 172.22.2.33:8080 | Max retries: 2 | Retry: 2 | Retry Interval: 60 seconds | Type: http";
    const expected =
      "[MONITOR] Monitor #16 'SABnzbd': Pending: connect EHOSTUNREACH 172.22.2.33:8080 | Max retries: 2 | Retry: 2 | Retry Interval: 60 seconds | Type: http";
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims leading ISO timestamp and bracketed level while preserving metadata tag and JSON (Example 3)', () => {
    const input =
      '2026-09-12T05:33:55.369Z [error][Plex.TV Metadata API]: Failed to retrieve watchlist items {"errorMessage":"Request failed with status code 504"}';
    const expected =
      '[Plex.TV Metadata API]: Failed to retrieve watchlist items {"errorMessage":"Request failed with status code 504"}';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('preserves message with application tag but no timestamp or level (Example 4)', () => {
    const input =
      'logshed: Could not download icon https://raw.githubusercontent.com/logshed/logshed/main/assets/logshed-logo.png';
    expect(cleanLogMessageForDisplay(input)).toBe(input);
  });

  it('trims slash datetime and bracketed level from nginx error log (Example 5)', () => {
    const input =
      '2026/09/12 07:43:00 [error] 2360609#2360609: *268403 open() "/usr/local/emhttp/plugins/dynamix.docker.manager/images/question.png" failed';
    const expected =
      '2360609#2360609: *268403 open() "/usr/local/emhttp/plugins/dynamix.docker.manager/images/question.png" failed';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims bracketed UTC timestamp from Technitium DNS server log', () => {
    const input =
      '[2026-09-12 07:48:57 UTC] [172.22.2.3:40424] [TCP] DNS Server received zone transfer request for zone: cluster-catalog.cluster.lan.benhorner.co.uk';
    const expected =
      '[172.22.2.3:40424] [TCP] DNS Server received zone transfer request for zone: cluster-catalog.cluster.lan.benhorner.co.uk';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims comma-millis timestamp and bracketed INFO from paperless-ngx log while preserving celery subsystem tag', () => {
    const input =
      '[2026-09-12 08:47:00,011] [INFO] [celery.worker.strategy] Task paperless_mail.tasks.process_mail_accounts[ed70640c-c25c-42b0-96a8-a6c765de24b4] received';
    const expected =
      '[celery.worker.strategy] Task paperless_mail.tasks.process_mail_accounts[ed70640c-c25c-42b0-96a8-a6c765de24b4] received';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims named timezone timestamp and LOG: from PostgreSQL log while preserving session PID', () => {
    const input = '2026-09-12 08:50:39.344 BST [27] LOG:  checkpoint starting: time';
    const expected = '[27] checkpoint starting: time';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('trims named timezone timestamp and unbracketed LOG: from PostgreSQL log', () => {
    const input = '2026-09-12 08:50:39.344 BST LOG:  checkpoint starting: time';
    const expected = 'checkpoint starting: time';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('preserves plain log message starting with the word Log when not followed by a colon', () => {
    const input = 'Log file rotated successfully';
    expect(cleanLogMessageForDisplay(input)).toBe(input);
  });

  it('cleans logfmt message extracting msg content and stripping time/level', () => {
    const input = 'time="2026-10-03T14:56:31Z" level=info msg="Successfully refreshed custom fields cache with 0 fields."';
    const expected = 'Successfully refreshed custom fields cache with 0 fields.';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('cleans Maintainerr application-prefixed line stripping app tag, slash date, and INFO bracket', () => {
    const input = "[maintainerr] | 03/10/2026 16:00:33  [INFO] [RuleExecutorService] Execution of rules for 'Never Watched by Anyone' done.";
    const expected = "[RuleExecutorService] Execution of rules for 'Never Watched by Anyone' done.";
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('cleans Valkey / Redis line stripping pid:role, date, and warning symbol', () => {
    const input = '1:M 02 Oct 2026 11:47:57.745 # Warning: No config file specified, using the default config. In order to specify a config file use valkey-server /path/to/valkey.conf';
    const expected = 'No config file specified, using the default config. In order to specify a config file use valkey-server /path/to/valkey.conf';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });

  it('cleans Plex Scan message preserving subsystem tag and message body', () => {
    const input = '2026-10-03T15:40:00.087Z [info][Plex Scan]: Recently Added Scan Complete';
    const expected = '[Plex Scan]: Recently Added Scan Complete';
    expect(cleanLogMessageForDisplay(input)).toBe(expected);
  });
});

describe('toLocalDatetimeInputString', () => {
  it('handles null, undefined, empty string, and invalid date gracefully', () => {
    expect(toLocalDatetimeInputString(null)).toBe('');
    expect(toLocalDatetimeInputString(undefined)).toBe('');
    expect(toLocalDatetimeInputString('')).toBe('');
    expect(toLocalDatetimeInputString('invalid-date')).toBe('');
  });

  it('formats an ISO UTC string into local YYYY-MM-DDTHH:mm representation', () => {
    // Construct a local date at 06:57 on 2026-09-16
    const localDate = new Date(2026, 8, 16, 6, 57);
    const isoString = localDate.toISOString();
    expect(toLocalDatetimeInputString(isoString)).toBe('2026-09-16T06:57');
  });

  it('pads single-digit months, days, hours, and minutes with leading zeroes', () => {
    const localDate = new Date(2026, 0, 5, 4, 8);
    const isoString = localDate.toISOString();
    expect(toLocalDatetimeInputString(isoString)).toBe('2026-01-05T04:08');
  });
});

describe('fromLocalDatetimeInputString', () => {
  it('handles null, undefined, empty string, and invalid strings gracefully', () => {
    expect(fromLocalDatetimeInputString(null)).toBeUndefined();
    expect(fromLocalDatetimeInputString(undefined)).toBeUndefined();
    expect(fromLocalDatetimeInputString('')).toBeUndefined();
    expect(fromLocalDatetimeInputString('   ')).toBeUndefined();
    expect(fromLocalDatetimeInputString('invalid-datetime')).toBeUndefined();
    expect(fromLocalDatetimeInputString('2026-09-16')).toBeUndefined();
  });

  it('converts a local YYYY-MM-DDTHH:mm string into a UTC ISO string', () => {
    const localString = '2026-09-16T06:57';
    const expectedIso = new Date(2026, 8, 16, 6, 57).toISOString();
    expect(fromLocalDatetimeInputString(localString)).toBe(expectedIso);
  });

  it('correctly round-trips with toLocalDatetimeInputString across local time', () => {
    const localString = '2026-09-16T06:57';
    const isoString = fromLocalDatetimeInputString(localString);
    expect(isoString).toBeDefined();
    expect(toLocalDatetimeInputString(isoString)).toBe(localString);
  });
});

describe('slugify', () => {
  it('converts rule names to kebab-case filenames', () => {
    expect(slugify('SSH Brute-Force')).toBe('ssh-brute-force');
    expect(slugify('High Ingestion Rate (Log Storm)')).toBe('high-ingestion-rate-log-storm');
    expect(slugify('  noisy_daemon  ')).toBe('noisy_daemon');
  });

  it('falls back to rule for empty or non-alphanumeric text', () => {
    expect(slugify('')).toBe('rule');
    expect(slugify('   ')).toBe('rule');
    expect(slugify('!@#$%^')).toBe('rule');
  });
});

describe('downloadBlob', () => {
  it('triggers browser download with object url and cleans up', () => {
    let clicked = false;
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => 'blob:mock-url');
    URL.revokeObjectURL = vi.fn();

    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      clicked = true;
    });

    const blob = new Blob(['{}'], { type: 'application/json' });
    downloadBlob(blob, 'test-export.json');

    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
    expect(clicked).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');

    clickSpy.mockRestore();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  });
});

describe('formatMaintenanceTime', () => {
  it('handles null, undefined, or empty string gracefully', () => {
    expect(formatMaintenanceTime(null)).toBe('');
    expect(formatMaintenanceTime(undefined)).toBe('');
    expect(formatMaintenanceTime('')).toBe('');
  });

  it('formats valid ISO datetime string', () => {
    const formatted = formatMaintenanceTime('2026-09-26T14:30:00Z');
    expect(formatted).toBeTruthy();
    expect(typeof formatted).toBe('string');
  });

  it('returns raw string if parsing fails', () => {
    expect(formatMaintenanceTime('invalid-date')).toBe('invalid-date');
  });
});

describe('formatLocalTimestamp', () => {
  it('handles null, undefined, or empty string gracefully', () => {
    expect(formatLocalTimestamp(null)).toBe('');
    expect(formatLocalTimestamp(undefined)).toBe('');
    expect(formatLocalTimestamp('')).toBe('');
  });

  it('returns raw string if parsing fails', () => {
    expect(formatLocalTimestamp('not-a-date')).toBe('not-a-date');
  });

  it('formats a date to local YYYY-MM-DD HH:mm:ss when includeSeconds is true (default)', () => {
    const d = new Date(2026, 9, 6, 14, 38, 53);
    const iso = d.toISOString();
    expect(formatLocalTimestamp(iso)).toBe('2026-10-06 14:38:53');
  });

  it('formats a date to local YYYY-MM-DD HH:mm when includeSeconds is false', () => {
    const d = new Date(2026, 9, 6, 14, 38, 53);
    const iso = d.toISOString();
    expect(formatLocalTimestamp(iso, false)).toBe('2026-10-06 14:38');
  });
});


