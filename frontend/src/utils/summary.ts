/**
 * Extracts a clean, plain-text summary from AI diagnostic Markdown response.
 * Strips '## Summary' header, section markers, code backticks, bold/italic,
 * list bullets, and collapses multi-line whitespace into a clean one-line preview.
 */
export function extractCleanSummary(text: string): string {
  if (!text) return '';

  let summary: string;
  // Try extracting text between ## Summary and the next section (## Root Cause / ## Actionable)
  const match = text.match(/##\s*Summary(?:\s*Analysis)?[:\s-]*([\s\S]*?)(?=##\s*Root\s*Cause|##\s*Actionable|$)/i);
  if (match && match[1]?.trim()) {
    summary = match[1].trim();
  } else {
    // Fallback: take first paragraph / block of text before any headers
    const parts = text.split(/\n\s*\n/);
    summary = parts[0]?.trim() || text.trim();
  }

  // Strip common Markdown formatting:
  const clean = summary
    .replace(/^#+\s*/gm, '') // headings (# Heading)
    .replace(/\*\*([^*]+)\*\*/g, '$1') // bold **text**
    .replace(/__([^_]+)__/g, '$1') // bold __text__
    .replace(/\*([^*]+)\*/g, '$1') // italic *text*
    .replace(/_([^_]+)_/g, '$1') // italic _text_
    .replace(/`([^`]+)`/g, '$1') // inline code `code`
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // links [text](url)
    .replace(/^[-*•]\s+/gm, '') // list bullets
    .replace(/^\d+\.\s+/gm, '') // numbered lists
    .replace(/\s+/g, ' ') // collapse multi-line/whitespace into single space
    .trim();

  return clean || text.replace(/\s+/g, ' ').trim();
}
