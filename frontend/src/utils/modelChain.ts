/** The fallback models, stored as comma-separated text, as a list without blanks. */
export function parseFallbackModels(text: string): string[] {
  return text ? text.split(',').map((s) => s.trim()).filter(Boolean) : [];
}

/**
 * Moving the first fallback up swaps it with the primary model. Returns the new primary model and
 * fallbacks (the old primary becomes the first fallback unless it is blank or already listed), or
 * null when there is no fallback.
 */
export function promoteFirstFallback(
  fallbacks: string[],
  primary: string
): { primary: string; fallbacks: string[] } | null {
  const [promoted, ...rest] = fallbacks;
  if (!promoted) return null;
  const demoted = primary.trim();
  return { primary: promoted, fallbacks: demoted && !rest.includes(demoted) ? [demoted, ...rest] : rest };
}
