import React, { useState } from 'react';
import { Check, Info } from 'lucide-react';
import { DockerEndpoint, LogEntry } from '../../types.ts';
import { Button } from '../common/Button.tsx';

interface ReportedHostNoteProps {
  log: LogEntry;
  /** Configured host aliases by address */
  hostAliases: Record<string, string>;
  dockerEndpoints: DockerEndpoint[];
  /** Saves the reported name as the address's alias; the drawer closes when it resolves */
  onMakeAlias?: (ip: string, name: string) => Promise<void>;
}

/** Which configured host uses name (letter case ignored), in words, or null when none does any more. */
function nameOwner(name: string, hostAliases: Record<string, string>, dockerEndpoints: DockerEndpoint[]): string | null {
  const folded = name.toLowerCase();
  const ip = Object.keys(hostAliases).find((a) => hostAliases[a].toLowerCase() === folded);
  if (ip) return `the alias of ${ip}`;
  const endpoint = dockerEndpoints.find((ep) => ep.source_alias?.toLowerCase() === folded);
  if (endpoint) return `the name of the Docker host ${endpoint.name}`;
  if (folded === 'docker') return 'the name of the local Docker host';
  if (folded === 'logshed') return "the name of LogShed's own logs";
  return null;
}

/**
 * The log drawer's note on a host name the sender reported for itself (its address has no alias):
 * either the name is shown and marked as self-reported, with a one-click "Make it the alias", or a
 * configured host already uses it and the log is shown under its address.
 */
export const ReportedHostNote: React.FC<ReportedHostNoteProps> = ({ log, hostAliases, dockerEndpoints, onMakeAlias }) => {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reported = log.reported_host;
  if (!reported || hostAliases[log.source_ip]) return null;

  const owner = log.host_self_reported ? null : nameOwner(reported, hostAliases, dockerEndpoints);

  const makeAlias = async () => {
    if (!onMakeAlias) return;
    setSaving(true);
    setError(null);
    try {
      await onMakeAlias(log.source_ip, reported);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The alias could not be saved.');
      setSaving(false);
    }
  };

  return (
    <div
      data-testid="reported-host-note"
      className="flex flex-col sm:flex-row sm:items-center gap-3 p-3 rounded-lg border border-line bg-canvas"
    >
      <Info className="hidden sm:block w-4 h-4 shrink-0 text-accent-400" aria-hidden="true" />
      <div className="flex-1 min-w-0 text-fg-soft leading-relaxed">
        {log.host_self_reported ? (
          <p>
            <span className="font-mono text-fg">{reported}</span> is the name this device reports for itself.{' '}
            {log.source_ip} has no alias in LogShed, so the name is shown as sent and marked as self-reported.
          </p>
        ) : (
          <p>
            This device calls itself <span className="font-mono text-fg">{reported}</span>
            {owner ? `, which is ${owner}` : ', a name another host used when this log arrived'}. Its logs are shown
            under its address, <span className="font-mono text-fg">{log.source_ip}</span>, so they do not mix with
            that host's.
          </p>
        )}
        {error && (
          <p role="alert" className="mt-1 text-danger-400">
            {error}
          </p>
        )}
      </div>
      {log.host_self_reported && onMakeAlias && (
        <Button
          onClick={makeAlias}
          disabled={saving}
          className="justify-center shrink-0"
          title={`Save ${reported} as the alias of ${log.source_ip}`}
        >
          <Check className="w-3.5 h-3.5 text-accent-400" aria-hidden="true" />
          <span>{saving ? 'Saving...' : 'Make it the alias'}</span>
        </Button>
      )}
    </div>
  );
};
