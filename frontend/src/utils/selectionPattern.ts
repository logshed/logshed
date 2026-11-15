/**
 * "Use selection as pattern": turns text the user selected in a log line into a rule pattern that
 * matches exactly that text, in the rule's own pattern language.
 */
import React, { useCallback, useState } from 'react';
import { MESSAGE_PATTERN_MAX_LENGTH } from './fieldLimits.ts';

/** Text as a regular expression that matches that text and nothing else (Python's regex and JavaScript alike). */
export function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export interface SelectionPattern {
  pattern: string;
  isRegex: boolean;
}

/**
 * An alert rule's pattern for the selected text: escaped when the rule is a regular expression,
 * as written when it is plain text (where every character means itself). A lone * would match
 * every log as plain text, so it becomes an escaped regular expression.
 */
export function alertPatternFromSelection(text: string, isRegex: boolean): SelectionPattern {
  const clean = text.trim();
  if (isRegex || clean === '*') return { pattern: escapeRegex(clean), isRegex: true };
  return { pattern: clean, isRegex: false };
}

/**
 * A drop rule's pattern for the selected text. Its plain text reads * and ? as wildcards, so text
 * holding either becomes an escaped regular expression instead; other text stays plain.
 */
export function dropPatternFromSelection(text: string, isRegex: boolean): SelectionPattern {
  const clean = text.trim();
  if (isRegex || /[*?]/.test(clean)) return { pattern: escapeRegex(clean), isRegex: true };
  return { pattern: clean, isRegex: false };
}

/** Why a selection cannot be used as a pattern, or null when it can (an empty one is just disabled). */
export function selectionProblem(text: string, pattern: string): string | null {
  if (/[\r\n]/.test(text.trim())) return 'Select within one line: a pattern cannot hold a line break.';
  if (pattern.length > MESSAGE_PATTERN_MAX_LENGTH) {
    return `The selection is too long for a pattern (at most ${MESSAGE_PATTERN_MAX_LENGTH.toLocaleString()} characters).`;
  }
  return null;
}

/**
 * The selection in a log line's text box and the pattern it gives. onSelect keeps it up to date;
 * use() returns the pattern to put in and remembers it, so that ticking or unticking the regular
 * expression box while the pattern is still that one rewrites it for the new mode (toggled());
 * reset() forgets both, for a dialog that opens again.
 */
export function useSelectionPattern(
  fromSelection: (text: string, isRegex: boolean) => SelectionPattern,
  isRegex: boolean
) {
  const [selected, setSelected] = useState('');
  const [inserted, setInserted] = useState<{ text: string; pattern: string } | null>(null);
  const candidate = fromSelection(selected, isRegex);
  const problem = candidate.pattern ? selectionProblem(selected, candidate.pattern) : null;

  const onSelect = (e: React.SyntheticEvent<HTMLTextAreaElement | HTMLInputElement>) => {
    const box = e.currentTarget;
    setSelected(box.value.slice(box.selectionStart ?? 0, box.selectionEnd ?? 0));
  };
  const use = (): SelectionPattern => {
    setInserted({ text: selected.trim(), pattern: candidate.pattern });
    return candidate;
  };
  /** The pattern for the new mode when the current one is still the inserted selection, else null. */
  const toggled = (currentPattern: string, nextIsRegex: boolean): string | null => {
    if (!inserted || currentPattern !== inserted.pattern) return null;
    const pattern = nextIsRegex ? escapeRegex(inserted.text) : inserted.text;
    setInserted({ text: inserted.text, pattern });
    return pattern;
  };
  // Stable, so a dialog's open effect can list it
  const reset = useCallback(() => {
    setSelected('');
    setInserted(null);
  }, []);
  return { candidate, problem, canUse: Boolean(candidate.pattern) && !problem, onSelect, use, toggled, reset };
}
