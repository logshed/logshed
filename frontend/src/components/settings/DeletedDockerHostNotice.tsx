import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';
import { Button } from '../common/Button.tsx';

export interface DeletedDockerHost {
  /** The alias the log carries (a former Docker host's, or the built-in `docker`). */
  name: string;
  /** The log's source address (`docker` for logs from a host with no address). */
  ip: string;
}

export interface DeletedDockerHostNoticeProps {
  host: DeletedDockerHost;
  onClose: () => void;
  /** Re-add Docker Host: the Docker host dialog, filled in from this host. */
  onReAdd: (initial: { name: string; source_alias: string; url: string }) => void;
  /** Add Syslog Host Alias for the address (not offered for `docker`). */
  onAddSyslogAlias: (ip: string) => void;
}

/**
 * The log drawer's Configure Docker Host for a log whose Docker host is no longer configured:
 * says so, and offers to re-add the host or to name its address as a syslog host instead.
 */
export const DeletedDockerHostNotice: React.FC<DeletedDockerHostNoticeProps> = ({ host, onClose, onReAdd, onAddSyslogAlias }) => (
  <Modal isOpen onClose={onClose} title="Docker Host Not Configured" maxWidth="max-w-md">
    <div className="space-y-4 text-xs font-sans">
      <div className="flex items-start gap-3 p-3 bg-warning-950/30 border border-warning-800/60 rounded-lg text-fg">
        <AlertTriangle className="w-5 h-5 text-warning-400 shrink-0 mt-0.5" />
        <div className="space-y-2 flex-1">
          <p className="font-semibold text-fg-strong text-xs">Docker host endpoint not found</p>
          <p className="text-fg-muted leading-relaxed">
            The Docker host endpoint for <span className="font-mono text-fg font-semibold">{host.name}</span> is no longer
            configured in LogShed. You can re-add it as a Docker host or map its IP address as a Syslog host alias.
          </p>
          <p className="text-fg-muted leading-relaxed">
            Its old container logs keep their Docker label either way: a Syslog host alias applies only to the syslog logs
            from that address.
          </p>
          <div className="p-2.5 rounded-sm bg-canvas/80 border border-line font-mono text-[11px] text-fg-soft space-y-1">
            <div>
              <span className="text-fg-muted">Host / Alias:</span> {host.name}
            </div>
            <div>
              <span className="text-fg-muted">Source IP:</span> {host.ip}
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-end gap-2 pt-2 border-t border-line">
        <Button onClick={onClose}>Close</Button>
        {host.ip && host.ip !== 'docker' && <Button onClick={() => onAddSyslogAlias(host.ip)}>Add Syslog Host Alias</Button>}
        <Button
          variant="primary"
          onClick={() =>
            onReAdd({
              name: host.name,
              source_alias: host.name,
              url: host.ip && host.ip !== 'docker' ? `tcp://${host.ip}:2375` : 'unix:///var/run/docker.sock',
            })
          }
        >
          Re-add Docker Host
        </Button>
      </div>
    </div>
  </Modal>
);
