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
import { useMediaQuery } from '../../utils/hooks.ts';
import { Modal } from '../common/Modal.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { DockerHostFormModal } from './DockerHostFormModal.tsx';
import { useAlias } from '../../context/AliasContext.tsx';
import { Badge } from '../common/Badge.tsx';
import { Button, IconButton } from '../common/Button.tsx';
import { Card, CardHeader, SECTION_TITLE_CLASS } from '../common/Card.tsx';
import { TableHead } from '../common/Table.tsx';

export interface DockerHostsTabProps {
  initialEditEndpointId?: number | null;
  initialAddHost?: { name?: string; url?: string; source_alias?: string } | null;
  initialDeletedHostInfo?: { name: string; ip: string } | null;
  onHostSaved?: () => void;
  onAddSyslogAlias?: (ip: string) => void;
  onClearSourcesState?: () => void;
}

/**
 * A Docker host's state: disabled, connected (with its running containers on a phone, where the table's
 * Containers column is missing), failing (with the error as its tooltip) or reconnecting.
 */
const HostStatusBadge: React.FC<{ host: DockerEndpoint; showContainers?: boolean }> = ({ host, showContainers = false }) => {
  if (!host.enabled) return <Badge>Disabled</Badge>;
  if (host.is_connected) {
    return <Badge tone="success">{showContainers ? `Connected (${host.active_containers ?? 0} active)` : 'Connected'}</Badge>;
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

export const DockerHostsTab: React.FC<DockerHostsTabProps> = ({
  initialEditEndpointId,
  initialAddHost,
  initialDeletedHostInfo,
  onHostSaved,
  onAddSyslogAlias,
  onClearSourcesState,
}) => {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const { bumpAliasVersion } = useAlias();
  const [hosts, setHosts] = useState<DockerEndpoint[]>([]);
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

  // Deleted host notice modal state
  const [deletedHostNotice, setDeletedHostNotice] = useState<{ name: string; ip: string } | null>(
    initialDeletedHostInfo || null
  );

  useEffect(() => {
    if (initialDeletedHostInfo) {
      setDeletedHostNotice(initialDeletedHostInfo);
    }
  }, [initialDeletedHostInfo]);

  const loadHosts = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);
      const list = await fetchDockerEndpoints();
      setHosts(list);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load Docker endpoints.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadHosts();
  }, []);

  const openAddModal = (initial?: { name?: string; url?: string; source_alias?: string } | null) => {
    setFormModal((prev) => ({ key: (prev?.key ?? 0) + 1, host: null, initialAddHost: initial }));
  };

  const openEditModal = (host: DockerEndpoint) => {
    setFormModal((prev) => ({ key: (prev?.key ?? 0) + 1, host }));
  };

  const handleCloseModal = () => {
    setFormModal(null);
    if (onClearSourcesState) onClearSourcesState();
  };

  useEffect(() => {
    if (initialEditEndpointId && hosts.length > 0) {
      const targetHost = hosts.find((h) => h.id === initialEditEndpointId);
      if (targetHost) {
        openEditModal(targetHost);
      }
    }
  }, [initialEditEndpointId, hosts]);

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
                    <HostStatusBadge host={host} showContainers />
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
                  { label: 'Name' },
                  { label: 'Endpoint URL' },
                  { label: 'Host Alias' },
                  { label: 'Status', className: 'w-40 whitespace-nowrap' },
                  { label: 'Containers', align: 'center', className: 'w-28 whitespace-nowrap' },
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
                          <span className="font-semibold text-fg-strong">{host.name}</span>
                          {host.id === 1 && <Badge tone="accent">Primary</Badge>}
                          {host.tls_enabled && (
                            <Badge title="Mutual TLS configured">
                              <Lock className="w-2.5 h-2.5 text-accent-400" />
                              TLS
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className="py-3 px-4 font-mono text-[11px] text-fg-soft max-w-xs truncate" title={host.url}>
                        {host.url}
                      </td>
                      <td className="py-3 px-4">
                        <span className="font-mono text-[11px] text-fg-soft bg-canvas px-2 py-0.5 rounded-sm border border-line-subtle">
                          {host.source_alias}
                        </span>
                      </td>
                      <td className="py-3 px-4 w-40 whitespace-nowrap">
                        <div className="flex items-center gap-1.5 whitespace-nowrap">
                          <HostStatusBadge host={host} />
                          {host.enabled && host.is_connected && host.latency_ms != null && (
                            <span className="font-mono text-[10px] text-fg-muted shrink-0 whitespace-nowrap">
                              {Math.round(host.latency_ms)} ms
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 px-4 w-28 text-center whitespace-nowrap">
                        <span className="font-mono text-fg-soft">
                          {host.enabled && host.is_connected ? host.active_containers ?? 0 : '0'}
                        </span>
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
            if (onHostSaved) onHostSaved();
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

      {/* Notice Modal when navigated to a Docker host that has been deleted */}
      {deletedHostNotice && (
        <Modal
          isOpen={!!deletedHostNotice}
          onClose={() => {
            setDeletedHostNotice(null);
            if (onClearSourcesState) onClearSourcesState();
          }}
          title="Docker Host Not Configured"
          maxWidth="max-w-md"
        >
          <div className="space-y-4 text-xs font-sans">
            <div className="flex items-start gap-3 p-3 bg-warning-950/30 border border-warning-800/60 rounded-lg text-fg">
              <AlertTriangle className="w-5 h-5 text-warning-400 shrink-0 mt-0.5" />
              <div className="space-y-2 flex-1">
                <p className="font-semibold text-fg-strong text-xs">
                  Docker host endpoint not found
                </p>
                <p className="text-fg-muted leading-relaxed">
                  The Docker host endpoint for <span className="font-mono text-fg font-semibold">{deletedHostNotice.name}</span> is no longer configured in LogShed. You can re-add it as a Docker host or map its IP address as a Syslog host alias.
                </p>
                <p className="text-fg-muted leading-relaxed">
                  Its old container logs keep their Docker label either way: a Syslog host alias applies only to the syslog logs from that address.
                </p>
                <div className="p-2.5 rounded-sm bg-canvas/80 border border-line font-mono text-[11px] text-fg-soft space-y-1">
                  <div>
                    <span className="text-fg-muted">Host / Alias:</span> {deletedHostNotice.name}
                  </div>
                  <div>
                    <span className="text-fg-muted">Source IP:</span> {deletedHostNotice.ip}
                  </div>
                </div>
              </div>
            </div>

            <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-end gap-2 pt-2 border-t border-line">
              <Button
                onClick={() => {
                  setDeletedHostNotice(null);
                  if (onClearSourcesState) onClearSourcesState();
                }}
              >
                Close
              </Button>
              {onAddSyslogAlias && deletedHostNotice.ip && deletedHostNotice.ip !== 'docker' && (
                <Button
                  onClick={() => {
                    const targetIp = deletedHostNotice.ip;
                    setDeletedHostNotice(null);
                    if (onClearSourcesState) onClearSourcesState();
                    onAddSyslogAlias(targetIp);
                  }}
                >
                  Add Syslog Host Alias
                </Button>
              )}
              <Button
                variant="primary"
                onClick={() => {
                  const targetInfo = deletedHostNotice;
                  setDeletedHostNotice(null);
                  if (onClearSourcesState) onClearSourcesState();
                  openAddModal({
                    name: targetInfo.name,
                    source_alias: targetInfo.name,
                    url:
                      targetInfo.ip && targetInfo.ip !== 'docker'
                        ? `tcp://${targetInfo.ip}:2375`
                        : 'unix:///var/run/docker.sock',
                  });
                }}
              >
                Re-add Docker Host
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
