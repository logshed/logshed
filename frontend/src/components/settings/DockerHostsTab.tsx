import React, { useEffect, useRef, useState } from 'react';
import {
  Box,
  Plus,
  Trash2,
  Edit2,
  Play,
  CheckCircle2,
  XCircle,
  AlertCircle,
  AlertTriangle,
  RefreshCw,
  Lock,
  Power,
} from 'lucide-react';
import {
  DockerEndpoint,
  DockerEndpointTestResult,
} from '../../types.ts';
import {
  fetchDockerEndpoints,
  updateDockerEndpoint,
  deleteDockerEndpoint,
  testDockerEndpoint,
} from '../../api/dockerHosts.ts';
import { useMediaQuery, useNow, usePolling, useServerTimezone } from '../../utils/hooks.ts';
import { LastSeen } from '../common/LastSeen.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { DockerHostFormModal } from './DockerHostFormModal.tsx';
import { useAlias } from '../../context/AliasContext.tsx';
import { Badge } from '../common/Badge.tsx';
import { Button, IconButton } from '../common/Button.tsx';
import { Card, CardHeader, SECTION_TITLE_CLASS } from '../common/Card.tsx';
import { SaveModeNote } from '../common/SaveModeNote.tsx';
import { TableHead } from '../common/Table.tsx';

export interface DockerHostsTabProps {
  initialAddHost?: { name?: string; url?: string; source_alias?: string } | null;
}

/**
 * A Docker host's state: disabled, connected (with its running containers), failing (with the error
 * as its tooltip) or reconnecting.
 */
const HostStatusBadge: React.FC<{ host: DockerEndpoint }> = ({ host }) => {
  if (!host.enabled) return <Badge>Disabled</Badge>;
  if (host.is_connected) {
    return <Badge tone="success">{`Connected (${host.active_containers ?? 0} active)`}</Badge>;
  }
  if (host.last_error) {
    return (
      <Badge tone="danger" title={host.last_error} className="cursor-help">
        Error
      </Badge>
    );
  }
  return <Badge tone="warning">Reconnecting</Badge>;
};

function lastSeenById(list: DockerEndpoint[]): Record<number, string | null | undefined> {
  return Object.fromEntries(list.map((h) => [h.id, h.last_seen]));
}

export const DockerHostsTab: React.FC<DockerHostsTabProps> = ({ initialAddHost }) => {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const { bumpAliasVersion } = useAlias();
  const [hosts, setHosts] = useState<DockerEndpoint[]>([]);
  // Kept apart from hosts, so a refresh cannot reopen or reset an open dialog
  const [lastSeen, setLastSeen] = useState<Record<number, string | null | undefined>>({});
  const now = useNow(5000);
  const serverTimezone = useServerTimezone();
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Row testing feedback
  const [testingRowId, setTestingRowId] = useState<number | null>(null);
  const [rowTestResults, setRowTestResults] = useState<Record<number, DockerEndpointTestResult>>({});

  // Row enable toggles with a request in flight
  const [togglingIds, setTogglingIds] = useState<Set<number>>(new Set());
  const togglingIdsRef = useRef<Set<number>>(new Set());

  // Add / Edit dialog: open with the host being edited (null adds one); key starts each opening afresh
  const [formModal, setFormModal] = useState<{
    key: number;
    host: DockerEndpoint | null;
    initialAddHost?: { name?: string; url?: string; source_alias?: string } | null;
  } | null>(null);

  // Delete Modal
  const [hostToDelete, setHostToDelete] = useState<DockerEndpoint | null>(null);
  const [isDeleting, setIsDeleting] = useState<boolean>(false);

  const loadHosts = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);
      const list = await fetchDockerEndpoints();
      setHosts(list);
      setLastSeen(lastSeenById(list));
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load Docker endpoints.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadHosts();
  }, []);

  usePolling(() => {
    fetchDockerEndpoints()
      .then((list) => setLastSeen(lastSeenById(list)))
      .catch(() => {});
  }, 30_000);

  const openAddModal = (initial?: { name?: string; url?: string; source_alias?: string } | null) => {
    setFormModal((prev) => ({ key: (prev?.key ?? 0) + 1, host: null, initialAddHost: initial }));
  };

  const openEditModal = (host: DockerEndpoint) => {
    setFormModal((prev) => ({ key: (prev?.key ?? 0) + 1, host }));
  };

  const handleCloseModal = () => {
    setFormModal(null);
  };

  useEffect(() => {
    if (initialAddHost) {
      openAddModal(initialAddHost);
    }
  }, [initialAddHost]);

  const handleToggleEnabled = async (host: DockerEndpoint) => {
    // One enable/disable request per host at a time (double clicks would send overlapping PUTs)
    if (togglingIdsRef.current.has(host.id)) return;
    togglingIdsRef.current.add(host.id);
    setTogglingIds(new Set(togglingIdsRef.current));
    try {
      await updateDockerEndpoint(host.id, { enabled: !host.enabled });
      await loadHosts();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to update Docker host status.');
    } finally {
      togglingIdsRef.current.delete(host.id);
      setTogglingIds(new Set(togglingIdsRef.current));
    }
  };

  const handleRowTest = async (host: DockerEndpoint) => {
    setTestingRowId(host.id);
    try {
      const res = await testDockerEndpoint({
        endpoint_id: host.id,
        url: host.url,
        tls_enabled: host.tls_enabled,
        verify_hostname: host.verify_hostname ?? true,
        ca_cert: host.ca_cert || null,
        client_cert: host.client_cert || null,
      });
      setRowTestResults((prev) => ({ ...prev, [host.id]: res }));
    } catch (err: any) {
      setRowTestResults((prev) => ({
        ...prev,
        [host.id]: { success: false, error: err.message || 'Test failed' },
      }));
    } finally {
      setTestingRowId(null);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!hostToDelete) return;
    setIsDeleting(true);
    try {
      await deleteDockerEndpoint(hostToDelete.id);
      setHostToDelete(null);
      await loadHosts();
      bumpAliasVersion();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to delete Docker endpoint.');
      setHostToDelete(null);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Unified Docker Host Endpoints Card */}
      <Card padding="flush">
        <div className="p-3.5 sm:p-5 border-b border-line space-y-4">
          <CardHeader
            icon={<Box />}
            title="Docker Host Endpoints"
            description="Collect container logs across local Unix sockets, remote socket proxies, and mutual TLS daemons into a unified pipeline."
            note={<SaveModeNote mode="instant" />}
            actions={
              <>
                <Button
                  onClick={loadHosts}
                  disabled={isLoading}
                  icon={<RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />}
                >
                  Refresh
                </Button>
                <Button variant="primary" onClick={() => openAddModal()} icon={<Plus className="w-3.5 h-3.5" />}>
                  Add Docker Host
                </Button>
              </>
            }
          />

          {errorMsg && (
            <div className="p-3 rounded-lg border bg-danger-950/60 border-danger-800 text-danger-300 text-xs flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-danger-400 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}
        </div>

        {/* Configured Docker Hosts Sub-Section */}
        <div className="p-3.5 sm:px-5 sm:py-3 bg-canvas/40 border-b border-line-subtle flex items-center justify-between">
          <h4 className={SECTION_TITLE_CLASS}>Configured Docker Hosts ({hosts.length})</h4>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-xs text-fg-muted font-mono">
            Loading Docker hosts...
          </div>
        ) : hosts.length === 0 ? (
          <div className="p-8 text-center space-y-2">
            <Box className="w-8 h-8 text-fg-faint mx-auto" />
            <p className="text-xs font-medium text-fg-soft">No Docker hosts configured</p>
            <p className="text-xs text-fg-muted max-w-sm mx-auto">
              Add a remote Docker host or socket proxy to collect logs from other machines.
            </p>
          </div>
        ) : isMobile ? (
          /* Mobile Card View */
          <div className="block md:hidden divide-y divide-line-subtle">
            {hosts.map((host) => {
              const testResult = rowTestResults[host.id];
              return (
                <div key={`mobile-host-${host.id}`} className="p-4 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-semibold text-fg-strong">{host.name}</span>
                        {host.id === 1 && <Badge tone="accent">Primary</Badge>}
                        {host.tls_enabled && (
                          <Badge>
                            <Lock className="w-2.5 h-2.5 text-accent-400" />
                            TLS
                          </Badge>
                        )}
                      </div>
                      <div className="font-mono text-[11px] text-fg-muted truncate mt-1">
                        {host.url}
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      <IconButton size="sm" onClick={() => openEditModal(host)} title="Edit Host" label={`Edit host ${host.name}`}>
                        <Edit2 className="w-3.5 h-3.5" />
                      </IconButton>
                      {host.id !== 1 && (
                        <IconButton
                          size="sm"
                          variant="danger"
                          onClick={() => setHostToDelete(host)}
                          title="Delete Host"
                          label={`Delete host ${host.name}`}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </IconButton>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="text-fg-muted">Alias:</span>
                    <span className="font-mono text-fg-soft bg-canvas px-1.5 py-0.5 rounded-sm border border-line-subtle">
                      {host.source_alias}
                    </span>

                    <span className="text-fg-muted ml-2">Status:</span>
                    <HostStatusBadge host={host} />

                    <span className="ml-2 whitespace-nowrap">
                      <span className="text-fg-muted">Last seen: </span>
                      <span className="text-fg-soft">
                        <LastSeen at={lastSeen[host.id]} now={now} timeZone={serverTimezone} />
                      </span>
                    </span>
                  </div>

                  {testResult && (
                    <div
                      className={`text-[11px] p-2 rounded border flex items-center gap-1.5 ${
                        testResult.success
                          ? 'bg-success-950/40 border-success-800 text-success-300'
                          : 'bg-danger-950/40 border-danger-800 text-danger-300'
                      }`}
                    >
                      {testResult.success ? (
                        <>
                          <CheckCircle2 className="w-3.5 h-3.5 text-success-400 shrink-0" />
                          <span>Connected ({testResult.version ?? 'Docker'}, {Math.round(testResult.latency_ms ?? 0)} ms)</span>
                        </>
                      ) : (
                        <>
                          <XCircle className="w-3.5 h-3.5 text-danger-400 shrink-0" />
                          <span className="truncate">{testResult.error || 'Connection failed'}</span>
                        </>
                      )}
                    </div>
                  )}

                  <div className="flex items-center justify-between pt-1 border-t border-line-subtle">
                    <Button
                      size="sm"
                      variant={host.enabled ? 'secondary' : 'soft'}
                      onClick={() => handleToggleEnabled(host)}
                      disabled={togglingIds.has(host.id)}
                    >
                      {host.enabled ? 'Disable' : 'Enable'}
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => handleRowTest(host)}
                      disabled={testingRowId === host.id}
                      icon={<Play className="w-3 h-3 text-accent-400" />}
                    >
                      {testingRowId === host.id ? 'Testing...' : 'Test Connection'}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* Desktop Table View */
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <TableHead
                columns={[
                  { label: 'Name and Alias', className: 'whitespace-nowrap' },
                  { label: 'Endpoint URL' },
                  { label: 'Status', className: 'whitespace-nowrap' },
                  { label: 'Last seen', className: 'w-28 whitespace-nowrap' },
                  { label: 'Actions', align: 'right' },
                ]}
              />
              <tbody className="divide-y divide-line-subtle text-fg">
                {hosts.map((host) => {
                  const testResult = rowTestResults[host.id];
                  return (
                    <tr key={`host-${host.id}`} className="hover:bg-raised/40 transition">
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-fg-strong whitespace-nowrap truncate max-w-40" title={host.name}>
                            {host.name}
                          </span>
                          {host.id === 1 && <Badge tone="accent">Primary</Badge>}
                          {host.tls_enabled && (
                            <Badge title="Mutual TLS configured">
                              <Lock className="w-2.5 h-2.5 text-accent-400" />
                              TLS
                            </Badge>
                          )}
                        </div>
                        {/* The alias sits under the name, as on a phone, so the URL has room */}
                        <span
                          className="inline-block mt-1 max-w-40 truncate font-mono text-[11px] text-fg-soft bg-canvas px-2 py-0.5 rounded-sm border border-line-subtle"
                          title={`Host alias: ${host.source_alias}`}
                        >
                          {host.source_alias}
                        </span>
                      </td>
                      {/* Takes the width the other columns leave (max-w-0 w-full), so the table fits its card */}
                      <td className="py-3 px-4 font-mono text-[11px] text-fg-soft max-w-0 w-full truncate" title={host.url}>
                        {host.url}
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-1.5 whitespace-nowrap">
                          <HostStatusBadge host={host} />
                          {host.enabled && host.is_connected && host.latency_ms != null && (
                            <span className="font-mono text-[10px] text-fg-muted shrink-0 whitespace-nowrap">
                              {Math.round(host.latency_ms)} ms
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 px-4 w-28 text-fg-soft">
                        <LastSeen at={lastSeen[host.id]} now={now} timeZone={serverTimezone} />
                      </td>
                      <td className="py-3 px-4 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {testResult && (
                            <span
                              className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${
                                testResult.success
                                  ? 'bg-success-950/50 text-success-400 border-success-800'
                                  : 'bg-danger-950/50 text-danger-400 border-danger-800'
                              }`}
                              title={testResult.error || testResult.version || ''}
                            >
                              {testResult.success ? `${Math.round(testResult.latency_ms ?? 0)} ms` : 'Failed'}
                            </span>
                          )}

                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleRowTest(host)}
                            disabled={testingRowId === host.id}
                            title="Test Connection"
                            aria-label={`Test connection to ${host.name}`}
                            icon={<Play className={`w-3.5 h-3.5 ${testingRowId === host.id ? 'animate-pulse text-accent-400' : ''}`} />}
                          >
                            Test
                          </Button>

                          <IconButton
                            size="sm"
                            onClick={() => handleToggleEnabled(host)}
                            disabled={togglingIds.has(host.id)}
                            label={`${host.enabled ? 'Disable' : 'Enable'} host ${host.name}`}
                          >
                            <Power className={`w-3.5 h-3.5 ${host.enabled ? 'text-accent-400' : 'text-fg-faint'}`} />
                          </IconButton>

                          <IconButton size="sm" onClick={() => openEditModal(host)} title="Edit Host" label={`Edit host ${host.name}`}>
                            <Edit2 className="w-3.5 h-3.5" />
                          </IconButton>

                          {host.id !== 1 ? (
                            <IconButton
                              size="sm"
                              variant="danger"
                              onClick={() => setHostToDelete(host)}
                              title="Delete Host"
                              label={`Delete host ${host.name}`}
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </IconButton>
                          ) : (
                            <span className="w-6 inline-block" />
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Add / Edit Host Modal */}
      {formModal && (
        <DockerHostFormModal
          key={formModal.key}
          editingHost={formModal.host}
          initialAddHost={formModal.initialAddHost}
          onClose={handleCloseModal}
          onSaved={async () => {
            handleCloseModal();
            await loadHosts();
            bumpAliasVersion();
          }}
        />
      )}

      {/* Delete Confirmation Modal */}
      <ConfirmDialog
        isOpen={Boolean(hostToDelete)}
        title="Remove Docker Host"
        onCancel={() => setHostToDelete(null)}
        onConfirm={handleDeleteConfirm}
        cancelDisabled={isDeleting}
        confirmDisabled={isDeleting}
        confirmLabel={isDeleting ? 'Removing...' : 'Remove Host'}
      >
        <div className="p-3 rounded-lg border bg-warning-950/40 border-warning-800/80 text-warning-200 text-xs flex items-start gap-2.5">
          <AlertTriangle className="w-4 h-4 text-warning-400 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold text-warning-100">Confirm Removal</p>
            <p className="mt-1 text-fg-soft">
              Are you sure you want to remove Docker host{' '}
              <strong className="text-fg-strong">{hostToDelete?.name}</strong>? Its active container collection task will stop immediately.
            </p>
          </div>
        </div>
      </ConfirmDialog>

    </div>
  );
};
