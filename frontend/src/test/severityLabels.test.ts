import { describe, it, expect } from 'vitest';
import { moreSevereLabel, lessSevereLabel, compactMoreSevereLabel } from '../utils/severityLabels.ts';

describe('severityLabels', () => {
  it('builds the long "more severe" label', () => {
    expect(moreSevereLabel(4)).toBe('Warning (4) and more severe');
    // Nothing is more severe than Emergency, and Debug and more severe is every level (beta testers, 2026-11-26)
    expect(moreSevereLabel(0)).toBe('Emergency (0) only');
    expect(moreSevereLabel(7)).toBe('Debug (7): every level');
  });

  it('builds the long "less severe" label', () => {
    expect(lessSevereLabel(4)).toBe('Warning (4) and less severe');
    expect(lessSevereLabel(7)).toBe('Debug (7) only');
    expect(lessSevereLabel(0)).toBe('Emergency (0): every level');
  });

  it('builds the compact label with short names', () => {
    expect(compactMoreSevereLabel(4)).toBe('Warn+ (0-4)');
    expect(compactMoreSevereLabel(3)).toBe('Error+ (0-3)');
    expect(compactMoreSevereLabel(2)).toBe('Crit+ (0-2)');
    expect(compactMoreSevereLabel(7)).toBe('Debug+ (0-7)');
    expect(compactMoreSevereLabel(0)).toBe('Emerg (0)');
  });
});
