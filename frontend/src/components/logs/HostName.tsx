import React from 'react';
import { BadgeQuestionMark } from 'lucide-react';

/**
 * A host name as the Console shows it. A name the device reported for itself (its address has no
 * alias) gets a small question-mark badge after it, and "(self-reported)" for screen readers.
 * trimText trims the name to its letters (the text-trim utility), for a fixed-height chip that
 * holds its content in the middle; flat is the cheaper form for the Console's log rows.
 */
export const HostName: React.FC<{ name: string; selfReported?: boolean; trimText?: boolean; flat?: boolean }> = ({
  name,
  selfReported = false,
  trimText = false,
  flat = false,
}) => {
  if (!selfReported) return trimText ? <span className="text-trim">{name}</span> : <>{name}</>;
  if (trimText) {
    // In a chip: the name trimmed to its letters, and the icon in line with their middle
    return (
      <span className="inline-flex items-center gap-1 min-w-0 max-w-full">
        {/* Cut short sideways only: a vertical clip would cut the descenders the trim leaves outside the box */}
        <span className="overflow-x-clip text-ellipsis whitespace-nowrap text-trim">{name}</span>
        <BadgeQuestionMark className="w-3 h-3 shrink-0 text-fg-subtle" aria-hidden="true" />
        <span className="sr-only"> (self-reported)</span>
      </span>
    );
  }
  if (flat) {
    // In a log row (every row draws one): the name as plain text and the icon as one CSS-masked box after
    // it, no wrapper or SVG, which keeps a flood's row updates about a seventh cheaper to lay out and
    // render. A name too long for its cell is cut short with its icon; the row's hover text still says it.
    return (
      <>
        {name}
        <span className="self-reported-mark" aria-hidden="true" />
        <span className="sr-only"> (self-reported)</span>
      </>
    );
  }
  return (
    // In running text: on the text's baseline, so the name lines up with the text around it. The
    // icon sits beside a zero-width space at vertical-align middle, level with the middle of the letters
    // in any font; it stays visible when a long name is cut short.
    <span className="inline-flex items-baseline gap-1 min-w-0 max-w-full">
      <span className="truncate">{name}</span>
      {/* Not selectable, so a copied host name has no zero-width space in it */}
      <span className="shrink-0 select-none" aria-hidden="true">
        {'\u200b'}
        <BadgeQuestionMark className="inline w-3 h-3 align-middle text-fg-subtle" />
      </span>
      <span className="sr-only"> (self-reported)</span>
    </span>
  );
};

/** The hover text of a self-reported host name: which addresses sent it. */
export function selfReportedTitle(name: string, addresses: string[]): string {
  const list = addresses.join(', ');
  return addresses.length > 1
    ? `${name}: the name ${list} send themselves (no alias set)`
    : `${name}: the name ${list} sends itself (no alias set)`;
}
