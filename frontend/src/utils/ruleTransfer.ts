import React from 'react';
import { downloadBlob } from './formatters.ts';

/**
 * Export and import of alert and drop rules as JSON files, shared by both rule lists.
 */

export interface RuleFeedback {
  text: string;
  isError: boolean;
}

export interface RuleImportResult {
  imported: number;
  skipped: number;
  errors: string[];
}

/** The message shown after an import; it is an error only when errors kept every rule out. */
export function describeImportResult(res: RuleImportResult): RuleFeedback {
  if (res.errors && res.errors.length > 0) {
    return {
      text: `Imported ${res.imported}, skipped ${res.skipped}. Errors: ${res.errors.join('; ')}`,
      isError: res.imported === 0,
    };
  }
  return {
    text: `Imported ${res.imported} rule${res.imported === 1 ? '' : 's'} (${res.skipped} skipped).`,
    isError: false,
  };
}

/** Download an export as filename, or report failureText (or the server's message) when it fails. */
export async function downloadRuleExport(
  fetchExport: () => Promise<Blob>,
  filename: string,
  onFeedback: (feedback: RuleFeedback) => void,
  failureText: string
): Promise<void> {
  try {
    downloadBlob(await fetchExport(), filename);
  } catch (err: any) {
    onFeedback({ text: err.message || failureText, isError: true });
  }
}

/**
 * Read the JSON file chosen in a file input and send it to importRules. Reports the outcome through
 * onFeedback and calls onImported after the server answered. The input is cleared, so choosing the
 * same file again imports it again.
 */
export function importRuleFile(
  e: React.ChangeEvent<HTMLInputElement>,
  importRules: (payload: object) => Promise<RuleImportResult>,
  onFeedback: (feedback: RuleFeedback) => void,
  onImported: () => void
): void {
  const file = e.target.files?.[0];
  if (!file) return;
  e.target.value = '';

  const reader = new FileReader();
  reader.onload = async (event) => {
    try {
      const text = event.target?.result as string;
      const json = JSON.parse(text);
      const res = await importRules(json);
      onFeedback(describeImportResult(res));
      onImported();
    } catch (err: any) {
      onFeedback({
        text: err.message || 'Failed to import rules. Invalid JSON file.',
        isError: true,
      });
    }
  };
  reader.onerror = () => {
    onFeedback({
      text: 'Failed to read file.',
      isError: true,
    });
  };
  reader.readAsText(file);
}
