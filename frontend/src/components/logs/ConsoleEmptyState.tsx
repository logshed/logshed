import React from 'react';
import { FilterX, Inbox, Info } from 'lucide-react';
import { CopyButton } from '../common/CopyButton.tsx';

/** The syslog port LogShed uses when SYSLOG_PORT is not set. */
export const DEFAULT_SYSLOG_PORT = 1514;

/** Example command in the first-run card; the host part is a placeholder the user replaces. */
export function onboardingLoggerCommand(port: number = DEFAULT_SYSLOG_PORT): string {
  return `logger -n <logshed-host> -P ${port} "Hello from LogShed"`;
}

/**
 * Why the list is empty: the first page is loading, the filters match nothing, the database has no
 * logs at all (the first-run card), or it is waiting for new logs.
 */
export type ConsoleEmptyKind = 'loading' | 'no-match' | 'first-run' | 'waiting';

interface ConsoleEmptyStateProps {
  kind: ConsoleEmptyKind;
  onResetFilters: () => void;
  /** The listening port from settings; undefined until settings load */
  syslogPort?: number;
  /** Opens Settings on the Sources tab */
  onOpenSourcesSettings?: () => void;
}

const FirstRunCard: React.FC<Pick<ConsoleEmptyStateProps, 'syslogPort' | 'onOpenSourcesSettings'>> = ({
  syslogPort,
  onOpenSourcesSettings,
}) => {
  const command = onboardingLoggerCommand(syslogPort);
  return (
    <div className="flex items-center justify-center min-h-full p-4">
      <div className="max-w-lg w-full bg-surface border border-line rounded-lg p-5 space-y-3 font-sans text-xs text-fg-soft select-text">
        <div className="flex items-center gap-2">
          <Inbox className="w-5 h-5 text-accent-400 shrink-0" />
          <h2 className="text-sm font-semibold text-fg-strong">No logs yet</h2>
        </div>
        <p className="leading-relaxed">
          {syslogPort !== undefined
            ? `LogShed listens for syslog on port ${syslogPort} (UDP and TCP) inside the container.`
            : `LogShed listens for syslog on port ${DEFAULT_SYSLOG_PORT} (UDP and TCP) inside the container by default.`}{' '}
          Point your devices at the host port you mapped to it.
        </p>
        <div className="flex items-center gap-2 bg-canvas border border-line rounded-sm px-3 py-2">
          <code className="flex-1 min-w-0 font-mono text-[11px] text-fg break-all">{command}</code>
          <CopyButton
            text={command}
            title="Copy command"
            className="inline-flex items-center gap-1.5 px-2 py-1 rounded-sm text-[11px] font-medium text-fg-muted hover:text-fg hover:bg-raised transition cursor-pointer min-h-[32px] shrink-0"
          />
        </div>
        {onOpenSourcesSettings && (
          <button
            type="button"
            onClick={onOpenSourcesSettings}
            className="px-3 py-1.5 rounded-sm text-xs font-medium bg-accent-700 hover:bg-accent-600 text-on-fill transition cursor-pointer"
          >
            Set up Docker hosts
          </button>
        )}
        <p className="text-fg-muted">See Sending Logs in the documentation for device guides.</p>
      </div>
    </div>
  );
};

/** What the Console list shows when it has no rows. */
export const ConsoleEmptyState: React.FC<ConsoleEmptyStateProps> = ({
  kind,
  onResetFilters,
  syslogPort,
  onOpenSourcesSettings,
}) => {
  switch (kind) {
    case 'loading':
      return (
        <div className="flex items-center justify-center h-full text-fg-muted font-mono">
          Loading log history...
        </div>
      );
    case 'no-match':
      return (
        <div className="flex flex-col items-center justify-center h-full text-fg-muted font-mono space-y-3 px-4 text-center">
          <FilterX className="w-8 h-8 text-neutral-700" />
          <span>No logs match the current filters.</span>
          <button
            type="button"
            onClick={onResetFilters}
            className="px-3 py-1.5 rounded-sm text-xs font-sans font-medium bg-raised text-fg border border-line-strong hover:border-line-strongest hover:text-fg-max transition cursor-pointer"
          >
            Clear filters
          </button>
        </div>
      );
    case 'first-run':
      return <FirstRunCard syslogPort={syslogPort} onOpenSourcesSettings={onOpenSourcesSettings} />;
    case 'waiting':
      return (
        <div className="flex flex-col items-center justify-center h-full text-fg-muted font-mono space-y-2">
          <Info className="w-8 h-8 text-neutral-700" />
          <span>Waiting for new logs...</span>
        </div>
      );
  }
};
