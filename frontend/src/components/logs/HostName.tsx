import React from 'react';
import { BadgeQuestionMark } from 'lucide-react';

/**
 * A host name as the Console shows it. A name the device reported for itself (its address has no
 * alias) gets a small question-mark badge after it, and "(self-reported)" for screen readers.
 */
export const HostName: React.FC<{ name: string; selfReported?: boolean }> = ({ name, selfReported = false }) => {
  if (!selfReported) return <>{name}</>;
  return (
    <span className="inline-flex items-center gap-1 min-w-0 max-w-full align-bottom">
      <span className="truncate">{name}</span>
      <BadgeQuestionMark className="w-3 h-3 shrink-0 text-fg-subtle" aria-hidden="true" />
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
