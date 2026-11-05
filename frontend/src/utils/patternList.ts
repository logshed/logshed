/**
 * Drop rule host and app fields hold comma-separated pattern lists; the backend stores them the
 * same way (normalize_pattern_list in backend/app/core/utils.py).
 */

/** The patterns in a stored host or app list ("a, b"), trimmed, without empty parts or repeats in another case. */
export function splitPatternList(value: string | null | undefined): string[] {
  const parts: string[] = [];
  const seen = new Set<string>();
  for (const raw of (value || '').split(',')) {
    const part = raw.trim();
    if (part && !seen.has(part.toLowerCase())) {
      seen.add(part.toLowerCase());
      parts.push(part);
    }
  }
  return parts;
}

/** Patterns in the stored form of a host or app list ("a, b"); empty when there are none. */
export const joinPatternList = (parts: string[]): string => splitPatternList(parts.join(',')).join(', ');
