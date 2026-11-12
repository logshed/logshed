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

export interface DockerHostsTabProps {
  initialEditEndpointId?: number | null;
  initialAddHost?: { name?: string; url?: string; source_alias?: string } | null;
  initialDeletedHostInfo?: { name: string; ip: string } | null;
  onHostSaved?: () => void;
  onAddSyslogAlias?: (ip: string) => void;
  onClearSourcesState?: () => void;
}

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
      <section className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-md">
        <div className="p-3.5 sm:p-5 border-b border-dark-700 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
                <Box className="w-4 h-4 text-accent-500" />
                <span>Docker Host Endpoints</span>
              </h3>
              <p className="text-xs text-slate-400 mt-0.5 max-w-2xl">
                Collect container logs across local Unix sockets, remote socket proxies, and mutual TLS daemons into a unified pipeline.
              </p>
            </div>
            <div className="flex items-center gap-2 self-start sm:self-auto shrink-0">
              <button
                type="button"
                onClick={loadHosts}
                disabled={isLoading}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-700 border border-dark-600 rounded-lg transition cursor-pointer disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
                <span>Refresh</span>
              </button>
              <button
                type="button"
                onClick={() => openAddModal()}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-accent-700 hover:bg-accent-600 rounded-lg shadow-xs transition cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>Add Docker Host</span>
              </button>
            </div>
          </div>

          {errorMsg && (
            <div className="mt-4 p-3 rounded-lg border bg-red-950/60 border-red-800 text-red-300 text-xs flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}
        </div>

        {/* Configured Docker Hosts Sub-Section */}
        <div className="p-3.5 sm:px-5 sm:py-3 bg-dark-950/40 border-b border-dark-800 flex items-center justify-between">
          <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
            Configured Docker Hosts ({hosts.length})
          </h4>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-xs text-slate-400 font-mono">
            Loading Docker hosts...
          </div>
        ) : hosts.length === 0 ? (
          <div className="p-8 text-center space-y-2">
            <Box className="w-8 h-8 text-slate-600 mx-auto" />
            <p className="text-xs font-medium text-slate-300">No Docker hosts configured</p>
            <p className="text-xs text-slate-400 max-w-sm mx-auto">
              Add a remote Docker host or socket proxy to collect logs from other machines.
            </p>
          </div>
        ) : isMobile ? (
          /* Mobile Card View */
          <div className="block md:hidden divide-y divide-dark-800">
            {hosts.map((host) => {
              const testResult = rowTestResults[host.id];
              return (
                <div key={`mobile-host-${host.id}`} className="p-4 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-semibold text-slate-100">{host.name}</span>
                        {host.id === 1 && (
                          <span className="text-[10px] font-medium bg-accent-950/40 text-accent-400 border border-accent-800/60 px-1.5 py-0.5 rounded">
                            Primary
                          </span>
                        )}
                        {host.tls_enabled && (
                          <span className="text-[10px] font-medium bg-dark-800 text-slate-300 border border-dark-700 px-1.5 py-0.5 rounded flex items-center gap-1">
                            <Lock className="w-2.5 h-2.5 text-accent-400" />
                            TLS
                          </span>
                        )}
                      </div>
                      <div className="font-mono text-[11px] text-slate-400 truncate mt-1">
                        {host.url}
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        type="button"
                        onClick={() => openEditModal(host)}
                        className="w-8 h-8 sm:w-auto sm:h-auto sm:p-1 flex items-center justify-center text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                        title="Edit Host"
                      >
                        <Edit2 className="w-3.5 h-3.5" />
                      </button>
                      {host.id !== 1 && (
                        <button
                          type="button"
                          onClick={() => setHostToDelete(host)}
                          className="w-8 h-8 sm:w-auto sm:h-auto sm:p-1 flex items-center justify-center text-red-400 hover:text-red-300 hover:bg-red-950/40 rounded transition cursor-pointer"
                          title="Delete Host"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="text-slate-400">Alias:</span>
                    <span className="font-mono text-slate-300 bg-dark-950 px-1.5 py-0.5 rounded border border-dark-800">
                      {host.source_alias}
                    </span>

                    <span className="text-slate-400 ml-2">Status:</span>
                    {!host.enabled ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-dark-800 text-slate-400 border border-dark-700">
                        Disabled
                      </span>
                    ) : host.is_connected ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-950/60 text-emerald-400 border border-emerald-800">
                        Connected ({host.active_containers ?? 0} active)
                      </span>
                    ) : host.last_error ? (
                      <span
                        className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-red-950/60 text-red-400 border border-red-800"
                        title={host.last_error}
                      >
                        Error
                      </span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-amber-950/60 text-amber-400 border border-amber-800">
                        Reconnecting
                      </span>
                    )}
                  </div>

                  {testResult && (
                    <div
                      className={`text-[11px] p-2 rounded border flex items-center gap-1.5 ${
                        testResult.success
                          ? 'bg-emerald-950/40 border-emerald-800 text-emerald-300'
                          : 'bg-red-950/40 border-red-800 text-red-300'
                      }`}
                    >
                      {testResult.success ? (
                        <>
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                          <span>Connected ({testResult.version ?? 'Docker'}, {Math.round(testResult.latency_ms ?? 0)} ms)</span>
                        </>
                      ) : (
                        <>
                          <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />
                          <span className="truncate">{testResult.error || 'Connection failed'}</span>
                        </>
                      )}
                    </div>
                  )}

                  <div className="flex items-center justify-between pt-1 border-t border-dark-800">
                    <button
                      type="button"
                      onClick={() => handleToggleEnabled(host)}
                      disabled={togglingIds.has(host.id)}
                      className={`text-[11px] font-medium px-2 py-1 rounded transition cursor-pointer border disabled:opacity-50 ${
                        host.enabled
                          ? 'text-slate-400 hover:text-slate-200 border-dark-700 bg-dark-950'
                          : 'text-emerald-400 hover:text-emerald-300 border-emerald-800 bg-emerald-950/30'
                      }`}
                    >
                      {host.enabled ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRowTest(host)}
                      disabled={testingRowId === host.id}
                      className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-slate-300 bg-dark-800 hover:bg-dark-750 border border-dark-600 rounded transition cursor-pointer disabled:opacity-50"
                    >
                      <Play className="w-3 h-3 text-accent-400" />
                      <span>{testingRowId === host.id ? 'Testing...' : 'Test Connection'}</span>
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* Desktop Table View */
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="border-b border-dark-700/60 bg-dark-950/40 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                  <th className="py-2.5 px-4">Name</th>
                  <th className="py-2.5 px-4 font-mono">Endpoint URL</th>
                  <th className="py-2.5 px-4">Host Alias</th>
                  <th className="py-2.5 px-4 w-40 whitespace-nowrap">Status</th>
                  <th className="py-2.5 px-4 w-28 whitespace-nowrap text-center">Containers</th>
                  <th className="py-2.5 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-dark-800 text-slate-200">
                {hosts.map((host) => {
                  const testResult = rowTestResults[host.id];
                  return (
                    <tr key={`host-${host.id}`} className="hover:bg-dark-800/40 transition">
                      <td className="py-3 px-4">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-slate-100">{host.name}</span>
                          {host.id === 1 && (
                            <span className="text-[10px] font-medium bg-accent-950/40 text-accent-400 border border-accent-800/60 px-1.5 py-0.5 rounded">
                              Primary
                            </span>
                          )}
                          {host.tls_enabled && (
                            <span
                              className="text-[10px] font-medium bg-dark-800 text-slate-300 border border-dark-700 px-1.5 py-0.5 rounded flex items-center gap-1"
                              title="Mutual TLS configured"
                            >
                              <Lock className="w-2.5 h-2.5 text-accent-400" />
                              TLS
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 px-4 font-mono text-[11px] text-slate-300 max-w-xs truncate" title={host.url}>
                        {host.url}
                      </td>
                      <td className="py-3 px-4">
                        <span className="font-mono text-[11px] text-slate-300 bg-dark-950 px-2 py-0.5 rounded border border-dark-800">
                          {host.source_alias}
                        </span>
                      </td>
                      <td className="py-3 px-4 w-40 whitespace-nowrap">
                        {!host.enabled ? (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-dark-800 text-slate-400 border border-dark-700">
                            Disabled
                          </span>
                        ) : host.is_connected ? (
                          <div className="flex items-center gap-1.5 whitespace-nowrap">
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-950/60 text-emerald-400 border border-emerald-800 shrink-0">
                              Connected
                            </span>
                            {host.latency_ms != null && (
                              <span className="font-mono text-[10px] text-slate-400 shrink-0 whitespace-nowrap">
                                {Math.round(host.latency_ms)} ms
                              </span>
                            )}
                          </div>
                        ) : host.last_error ? (
                          <span
                            className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-red-950/60 text-red-400 border border-red-800 cursor-help whitespace-nowrap"
                            title={host.last_error}
                          >
                            Error
                          </span>
                        ) : (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-amber-950/60 text-amber-400 border border-amber-800 whitespace-nowrap">
                            Reconnecting
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4 w-28 text-center whitespace-nowrap">
                        <span className="font-mono text-slate-300">
                          {host.enabled && host.is_connected ? host.active_containers ?? 0 : '0'}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {testResult && (
                            <span
                              className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${
                                testResult.success
                                  ? 'bg-emerald-950/50 text-emerald-400 border-emerald-800'
                                  : 'bg-red-950/50 text-red-400 border-red-800'
                              }`}
                              title={testResult.error || testResult.version || ''}
                            >
                              {testResult.success ? `${Math.round(testResult.latency_ms ?? 0)} ms` : 'Failed'}
                            </span>
                          )}

                          <button
                            type="button"
                            onClick={() => handleRowTest(host)}
                            disabled={testingRowId === host.id}
                            className="inline-flex items-center gap-1 h-8 sm:h-auto px-2 sm:py-1 text-[11px] font-medium text-slate-400 hover:text-accent-400 hover:bg-dark-800 rounded transition cursor-pointer disabled:opacity-50"
                            title="Test Connection"
                            aria-label={`Test connection to ${host.name}`}
                          >
                            <Play className={`w-3.5 h-3.5 ${testingRowId === host.id ? 'animate-pulse text-accent-400' : ''}`} />
                            <span>Test</span>
                          </button>

                          <button
                            type="button"
                            onClick={() => handleToggleEnabled(host)}
                            disabled={togglingIds.has(host.id)}
                            className={`w-8 h-8 sm:w-auto sm:h-auto sm:p-1 flex items-center justify-center rounded transition cursor-pointer disabled:opacity-50 ${
                              host.enabled
                                ? 'text-accent-400 hover:text-accent-300 hover:bg-dark-800'
                                : 'text-slate-600 hover:text-slate-400 hover:bg-dark-800'
                            }`}
                            title={`${host.enabled ? 'Disable' : 'Enable'} host ${host.name}`}
                            aria-label={`${host.enabled ? 'Disable' : 'Enable'} host ${host.name}`}
                          >
                            <Power className="w-3.5 h-3.5" />
                          </button>

                          <button
                            type="button"
                            onClick={() => openEditModal(host)}
                            className="w-8 h-8 sm:w-auto sm:h-auto sm:p-1 flex items-center justify-center text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                            title="Edit Host"
                          >
                            <Edit2 className="w-3.5 h-3.5" />
                          </button>

                          {host.id !== 1 ? (
                            <button
                              type="button"
                              onClick={() => setHostToDelete(host)}
                              className="w-8 h-8 sm:w-auto sm:h-auto sm:p-1 flex items-center justify-center text-red-400 hover:text-red-300 hover:bg-red-950/40 rounded transition cursor-pointer"
                              title="Delete Host"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
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
      </section>

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
        bodyClassName="space-y-4"
        footerClassName="flex items-center justify-end gap-2 pt-2 border-t border-dark-700"
        cancelClassName="px-3 py-1.5 text-xs font-medium text-slate-400 hover:text-slate-200 transition cursor-pointer"
        confirmClassName="px-3.5 py-1.5 text-xs font-medium text-white bg-red-600 hover:bg-red-500 rounded-lg transition cursor-pointer disabled:opacity-50"
      >
        <div className="p-3 rounded-lg border bg-amber-950/40 border-amber-800/80 text-amber-200 text-xs flex items-start gap-2.5">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold text-amber-100">Confirm Removal</p>
            <p className="mt-1 text-slate-300">
              Are you sure you want to remove Docker host{' '}
              <strong className="text-slate-100">{hostToDelete?.name}</strong>? Its active container collection task will stop immediately.
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
            <div className="flex items-start gap-3 p-3 bg-amber-950/30 border border-amber-800/60 rounded-lg text-slate-200">
              <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
              <div className="space-y-2 flex-1">
                <p className="font-semibold text-slate-100 text-xs">
                  Docker host endpoint not found
                </p>
                <p className="text-slate-400 leading-relaxed">
                  The Docker host endpoint for <span className="font-mono text-slate-200 font-semibold">{deletedHostNotice.name}</span> is no longer configured in LogShed. You can re-add it as a Docker host or map its IP address as a Syslog host alias.
                </p>
                <p className="text-slate-400 leading-relaxed">
                  Its old container logs keep their Docker label either way: a Syslog host alias applies only to the syslog logs from that address.
                </p>
                <div className="p-2.5 rounded bg-dark-950/80 border border-dark-700 font-mono text-[11px] text-slate-300 space-y-1">
                  <div>
                    <span className="text-slate-400">Host / Alias:</span> {deletedHostNotice.name}
                  </div>
                  <div>
                    <span className="text-slate-400">Source IP:</span> {deletedHostNotice.ip}
                  </div>
                </div>
              </div>
            </div>

            <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-end gap-2 pt-2 border-t border-dark-700">
              <button
                type="button"
                onClick={() => {
                  setDeletedHostNotice(null);
                  if (onClearSourcesState) onClearSourcesState();
                }}
                className="px-3 py-1.5 text-xs bg-dark-800 hover:bg-dark-700 text-slate-300 border border-dark-600 rounded-lg transition cursor-pointer"
              >
                Close
              </button>
              {onAddSyslogAlias && deletedHostNotice.ip && deletedHostNotice.ip !== 'docker' && (
                <button
                  type="button"
                  onClick={() => {
                    const targetIp = deletedHostNotice.ip;
                    setDeletedHostNotice(null);
                    if (onClearSourcesState) onClearSourcesState();
                    onAddSyslogAlias(targetIp);
                  }}
                  className="px-3.5 py-1.5 text-xs bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-600 rounded-lg font-medium transition cursor-pointer"
                >
                  Add Syslog Host Alias
                </button>
              )}
              <button
                type="button"
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
                className="px-3.5 py-1.5 text-xs bg-accent-700 hover:bg-accent-600 text-white font-medium rounded-lg shadow transition cursor-pointer"
              >
                Re-add Docker Host
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
