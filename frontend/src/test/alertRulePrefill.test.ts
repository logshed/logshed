import { describe, it, expect } from 'vitest';
import { alertRulePrefillFromLog } from '../utils/alertRulePrefill.ts';
import { LogEntry } from '../types.ts';

const log: LogEntry = {
  id: 1,
  timestamp: '2026-11-15T10:00:00Z',
  received_at: '2026-11-15T10:00:00Z',
  source_ip: '192.168.1.9',
  source_alias: 'nas',
  app_name: 'nginx',
  facility: 1,
  severity: 3,
  message: '\u001b[31mNginx upstream connection timeout\u001b[0m',
  raw: '',
};

describe('alertRulePrefillFromLog', () => {
  it('names the rule after the app and filters to the host and the known app', () => {
    expect(alertRulePrefillFromLog(log, ['nginx', 'sshd'])).toEqual({
      name: 'Alert on nginx',
      filterApps: ['nginx'],
      filterHosts: ['nas'],
      referenceMessage: 'Nginx upstream connection timeout',
    });
  });

  it('leaves the app filter empty when the app is not among the known apps', () => {
    expect(alertRulePrefillFromLog({ ...log, app_name: 'mystery-app' }, ['nginx']).filterApps).toEqual([]);
  });

  it('names the rule after the host when the log has no app, and uses the address when the host has no name', () => {
    const prefill = alertRulePrefillFromLog({ ...log, app_name: '', source_alias: '' }, []);
    expect(prefill.name).toBe('Alert on 192.168.1.9');
    expect(prefill.filterHosts).toEqual(['192.168.1.9']);
  });
});
