/**
 * "Use selection as pattern": turns text the user selected in a log line into a rule pattern that
 * matches exactly that text, in the rule's own pattern language.
 */
import React, { useState } from 'react';

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
 * as written when it is plain text (where every character means itself).
 */
export function alertPatternFromSelection(text: string, isRegex: boolean): SelectionPattern {
  const clean = text.trim();
  return { pattern: isRegex ? escapeRegex(clean) : clean, isRegex };
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

/** The text selected in a text box, kept up to date by the returned onSelect handler. */
export function useSelectedText(): [string, (e: React.SyntheticEvent<HTMLTextAreaElement | HTMLInputElement>) => void] {
  const [selected, setSelected] = useState('');
  const onSelect = (e: React.SyntheticEvent<HTMLTextAreaElement | HTMLInputElement>) => {
    const box = e.currentTarget;
    const start = box.selectionStart ?? 0;
    const end = box.selectionEnd ?? 0;
    setSelected(box.value.slice(start, end));
  };
  return [selected, onSelect];
}
