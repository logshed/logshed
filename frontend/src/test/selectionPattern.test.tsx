import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { alertPatternFromSelection, dropPatternFromSelection, escapeRegex } from '../utils/selectionPattern.ts';
import { CreateDropRuleModal } from '../components/alerts/CreateDropRuleModal.tsx';
import * as aliasesApi from '../api/aliases.ts';

describe('patterns from a selection', () => {
  it('escapes every regular expression operator, so the pattern matches only the text', () => {
    const text = 'a.b*c+d?e^f$g{1}h(i)j|k[l]m\\n';
    const escaped = escapeRegex(text);
    expect(new RegExp(`^${escaped}$`).test(text)).toBe(true);
    expect(new RegExp(escaped).test('aXb')).toBe(false);
  });

  it('keeps an alert rule in its mode, trimmed', () => {
    expect(alertPatternFromSelection('  disk (sda) full ', false)).toEqual({ pattern: 'disk (sda) full', isRegex: false });
    expect(alertPatternFromSelection('disk (sda)', true)).toEqual({ pattern: 'disk \\(sda\\)', isRegex: true });
  });

  it('turns a drop rule selection holding a wildcard character into an escaped regular expression', () => {
    expect(dropPatternFromSelection('GET /healthz', false)).toEqual({ pattern: 'GET /healthz', isRegex: false });
    expect(dropPatternFromSelection('GET /status?full=1', false)).toEqual({ pattern: 'GET /status\\?full=1', isRegex: true });
    expect(dropPatternFromSelection('rm -rf /tmp/*', false)).toEqual({ pattern: 'rm -rf /tmp/\\*', isRegex: true });
    expect(dropPatternFromSelection('a.b', true)).toEqual({ pattern: 'a\\.b', isRegex: true });
  });
});

describe('Use selection as pattern in the drop rule dialog', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue([]);
  });

  const line = 'GET /status?full=1 from 10.0.0.7 took 12ms';
  const openFromLog = () => {
    render(<CreateDropRuleModal isOpen={true} onClose={vi.fn()} initialApp="nginx" initialMessage={line} />);
    const sample = screen.getByPlaceholderText('Paste a representative log line here...') as HTMLTextAreaElement;
    const select = (part: string) => {
      const start = line.indexOf(part);
      sample.setSelectionRange(start, start + part.length);
      fireEvent.select(sample);
    };
    return { select, use: screen.getByRole('button', { name: 'Use selection as pattern' }) };
  };
  const pattern = () => screen.getByPlaceholderText(/DHCPACK/) as HTMLInputElement;
  const regexBox = () => screen.getByLabelText('Interpret message pattern as Regular Expression') as HTMLInputElement;

  it('is disabled until part of the sample line is selected', () => {
    const { use } = openFromLog();
    expect(use).toBeDisabled();
  });

  it('puts plain text in as written and keeps the rule plain text', () => {
    const { select, use } = openFromLog();
    select('from 10.0.0.7');
    fireEvent.click(use);
    expect(pattern().value).toBe('from 10.0.0.7');
    expect(regexBox().checked).toBe(false);
  });

  it('switches to a regular expression for text holding ? or *, so they are not wildcards', () => {
    const { select, use } = openFromLog();
    select('GET /status?full=1');
    fireEvent.click(use);
    expect(pattern().value).toBe('GET /status\\?full=1');
    expect(regexBox().checked).toBe(true);
    expect(new RegExp(pattern().value, 'i').test(line)).toBe(true);
    expect(new RegExp(pattern().value, 'i').test('GET /statusXfull=1')).toBe(false);
  });

  it('escapes the selection when the rule is already a regular expression', () => {
    const { select, use } = openFromLog();
    fireEvent.click(regexBox());
    select('took 12ms');
    fireEvent.click(use);
    expect(pattern().value).toBe('took 12ms');
    select('10.0.0.7');
    fireEvent.click(use);
    expect(pattern().value).toBe('10\\.0\\.0\\.7');
    expect(regexBox().checked).toBe(true);
  });
});
