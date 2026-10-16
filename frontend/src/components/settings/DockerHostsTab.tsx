import React, { useEffect, useState } from 'react';
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
  Check,
} from 'lucide-react';
import {
  DockerEndpoint,
  DockerEndpointCreate,
  DockerEndpointUpdate,
  DockerEndpointTestResult,
} from '../../types.ts';
import {
  fetchDockerEndpoints,
  createDockerEndpoint,
  updateDockerEndpoint,
  deleteDockerEndpoint,
  testDockerEndpoint,
} from '../../api/dockerHosts.ts';
import { useMediaQuery } from '../../utils/hooks.ts';
import { Modal } from '../common/Modal.tsx';
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

  // Add / Edit Modal
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [editingHost, setEditingHost] = useState<DockerEndpoint | null>(null);
  const [name, setName] = useState<string>('');
  const [url, setUrl] = useState<string>('');
  const [sourceAlias, setSourceAlias] = useState<string>('docker');
  const [isAliasManuallyEdited, setIsAliasManuallyEdited] = useState<boolean>(false);
  const [excludeContainers, setExcludeContainers] = useState<string>('');
  const [enabled, setEnabled] = useState<boolean>(true);
  const [tlsEnabled, setTlsEnabled] = useState<boolean>(false);
  const [verifyHostname, setVerifyHostname] = useState<boolean>(true);
  const [caCert, setCaCert] = useState<string>('');
  const [clientCert, setClientCert] = useState<string>('');
  const [clientKey, setClientKey] = useState<string>('');
  const [clearClientKey, setClearClientKey] = useState<boolean>(false);

  const [modalTestLoading, setModalTestLoading] = useState<boolean>(false);
  const [modalTestResult, setModalTestResult] = useState<DockerEndpointTestResult | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [modalError, setModalError] = useState<string | null>(null);

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

  const slugifyAlias = (val: string): string => {
    return val
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64);
  };

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

  const openAddModal = () => {
    setEditingHost(null);
    setName('');
    setUrl('');
    setSourceAlias('docker');
    setIsAliasManuallyEdited(false);
    setExcludeContainers('');
    setEnabled(true);
    setTlsEnabled(false);
    setVerifyHostname(true);
    setCaCert('');
    setClientCert('');
    setClientKey('');
    setClearClientKey(false);
    setModalTestResult(null);
    setModalError(null);
    setIsModalOpen(true);
  };

  const openEditModal = (host: DockerEndpoint) => {
    setEditingHost(host);
    setName(host.name);
    setUrl(host.url);
    setSourceAlias(host.source_alias || 'docker');
    setIsAliasManuallyEdited(true);
    setExcludeContainers(host.exclude_containers || '');
    setEnabled(host.enabled);
    setTlsEnabled(host.tls_enabled);
    setVerifyHostname(host.verify_hostname ?? true);
    setCaCert(host.ca_cert || '');
    setClientCert(host.client_cert || '');
    setClientKey('');
    setClearClientKey(false);
    setModalTestResult(null);
    setModalError(null);
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setClearClientKey(false);
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
      openAddModal();
      if (initialAddHost.name) setName(initialAddHost.name);
      if (initialAddHost.url) setUrl(initialAddHost.url);
      if (initialAddHost.source_alias) {
        setSourceAlias(initialAddHost.source_alias);
        setIsAliasManuallyEdited(true);
      }
    }
  }, [initialAddHost]);

  const handleNameChange = (val: string) => {
    setName(val);
    if (!isAliasManuallyEdited && !editingHost) {
      setSourceAlias(slugifyAlias(val) || 'docker');
    }
  };

  const handleAliasChange = (val: string) => {
    setSourceAlias(val);
    setIsAliasManuallyEdited(true);
  };

  const handleTestModal = async () => {
    if (!url.trim()) {
      setModalError('URL is required to test connection.');
      return;
    }
    setModalTestLoading(true);
    setModalTestResult(null);
    setModalError(null);
    try {
      // Do not let the backend fall back to stored credentials the form is about to remove
      const certsCleared =
        (Boolean(editingHost?.ca_cert) && !caCert.trim()) ||
        (Boolean(editingHost?.client_cert) && !clientCert.trim());
      const res = await testDockerEndpoint({
        endpoint_id: editingHost?.id,
        url: url.trim(),
        tls_enabled: tlsEnabled,
        verify_hostname: verifyHostname,
        ca_cert: caCert.trim() || null,
        client_cert: clientCert.trim() || null,
        client_key: clearClientKey ? null : (clientKey.trim() || null),
        use_stored_secrets: !(clearClientKey || certsCleared),
      });
      setModalTestResult(res);
    } catch (err: any) {
      setModalTestResult({
        success: false,
        error: err.message || 'Connection test failed.',
      });
    } finally {
      setModalTestLoading(false);
    }
  };

  const handleSubmitModal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !url.trim()) {
      setModalError('Name and URL are required.');
      return;
    }

    setIsSubmitting(true);
    setModalError(null);
    try {
      if (editingHost) {
        const updatePayload: DockerEndpointUpdate = {
          name: name.trim(),
          url: url.trim(),
          source_alias: sourceAlias.trim() || 'docker',
          exclude_containers: excludeContainers.trim(),
          enabled,
          tls_enabled: tlsEnabled,
          verify_hostname: verifyHostname,
          // An empty string clears a stored certificate (null would keep it)
          ca_cert: caCert.trim(),
          client_cert: clientCert.trim(),
        };
        if (clearClientKey) {
          updatePayload.client_key = '';
        } else if (clientKey.trim()) {
          updatePayload.client_key = clientKey.trim();
        }
        await updateDockerEndpoint(editingHost.id, updatePayload);
      } else {
        const createPayload: DockerEndpointCreate = {
          name: name.trim(),
          url: url.trim(),
          source_alias: sourceAlias.trim() || 'docker',
          exclude_containers: excludeContainers.trim(),
          enabled,
          tls_enabled: tlsEnabled,
          verify_hostname: verifyHostname,
          ca_cert: caCert.trim() || null,
          client_cert: clientCert.trim() || null,
          client_key: clientKey.trim() || null,
        };
        await createDockerEndpoint(createPayload);
      }
      handleCloseModal();
      await loadHosts();
      bumpAliasVersion();
      if (onHostSaved) onHostSaved();
    } catch (err: any) {
      setModalError(err.message || 'Failed to save Docker host.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleToggleEnabled = async (host: DockerEndpoint) => {
    try {
      await updateDockerEndpoint(host.id, { enabled: !host.enabled });
      await loadHosts();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to update Docker host status.');
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
                onClick={openAddModal}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 rounded-lg shadow-xs transition cursor-pointer"
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
            <p className="text-xs text-slate-500 max-w-sm mx-auto">
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

                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={() => openEditModal(host)}
                        className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                        title="Edit Host"
                      >
                        <Edit2 className="w-3.5 h-3.5" />
                      </button>
                      {host.id !== 1 && (
                        <button
                          type="button"
                          onClick={() => setHostToDelete(host)}
                          className="p-1.5 text-red-400 hover:text-red-300 hover:bg-red-950/40 rounded transition cursor-pointer"
                          title="Delete Host"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="text-slate-500">Alias:</span>
                    <span className="font-mono text-slate-300 bg-dark-950 px-1.5 py-0.5 rounded border border-dark-800">
                      {host.source_alias}
                    </span>

                    <span className="text-slate-500 ml-2">Status:</span>
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
                      className={`text-[11px] font-medium px-2 py-1 rounded transition cursor-pointer border ${
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
                            className="p-1 text-slate-400 hover:text-accent-400 hover:bg-dark-800 rounded transition cursor-pointer disabled:opacity-50"
                            title="Test Connection"
                          >
                            <Play className={`w-3.5 h-3.5 ${testingRowId === host.id ? 'animate-pulse text-accent-400' : ''}`} />
                          </button>

                          <button
                            type="button"
                            onClick={() => handleToggleEnabled(host)}
                            className={`p-1 rounded transition cursor-pointer ${
                              host.enabled
                                ? 'text-slate-400 hover:text-slate-200 hover:bg-dark-800'
                                : 'text-emerald-400 hover:text-emerald-300 hover:bg-emerald-950/40'
                            }`}
                            title={host.enabled ? 'Disable Host' : 'Enable Host'}
                          >
                            <Check className={`w-3.5 h-3.5 ${host.enabled ? 'text-accent-400' : 'text-slate-600'}`} />
                          </button>

                          <button
                            type="button"
                            onClick={() => openEditModal(host)}
                            className="p-1 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                            title="Edit Host"
                          >
                            <Edit2 className="w-3.5 h-3.5" />
                          </button>

                          {host.id !== 1 ? (
                            <button
                              type="button"
                              onClick={() => setHostToDelete(host)}
                              className="p-1 text-red-400 hover:text-red-300 hover:bg-red-950/40 rounded transition cursor-pointer"
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
      <Modal
        isOpen={isModalOpen}
        onClose={handleCloseModal}
        title={editingHost ? 'Edit Docker Host' : 'Add Docker Host'}
        maxWidth="max-w-2xl"
      >
        <form onSubmit={handleSubmitModal} className="space-y-4">
          {modalError && (
            <div className="p-3 rounded-lg border bg-red-950/60 border-red-800 text-red-300 text-xs flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              <span>{modalError}</span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label htmlFor="host-name" className="block text-xs font-medium text-slate-300 mb-1">
                Host Name <span className="text-red-400">*</span>
              </label>
              <input
                id="host-name"
                type="text"
                placeholder="e.g. Unraid Server"
                value={name}
                onChange={(e) => handleNameChange(e.target.value)}
                required
                disabled={editingHost?.id === 1}
                className={`w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 ${
                  editingHost?.id === 1 ? 'opacity-60 cursor-not-allowed' : ''
                }`}
              />
              {editingHost?.id === 1 && (
                <p className="text-[11px] text-slate-500 mt-1">
                  Primary Docker endpoint name is fixed.
                </p>
              )}
            </div>

            <div>
              <label htmlFor="host-alias" className="block text-xs font-medium text-slate-300 mb-1">
                Host Alias <span className="text-red-400">*</span>
              </label>
              <input
                id="host-alias"
                type="text"
                placeholder="docker"
                value={sourceAlias}
                onChange={(e) => handleAliasChange(e.target.value)}
                required
                maxLength={64}
                className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
              />
              <p className="text-[11px] text-slate-500 mt-1">
                Label assigned to logs under the Host / IP column (defaults to host name).
              </p>
            </div>
          </div>

          <div>
            <label htmlFor="host-url" className="block text-xs font-medium text-slate-300 mb-1">
              Docker Endpoint URL <span className="text-red-400">*</span>
            </label>
            <input
              id="host-url"
              type="text"
              placeholder="unix:///var/run/docker.sock or tcp://192.168.1.50:2375"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
            <p className="text-[11px] text-slate-500 mt-1">
              Supports local socket paths or remote TCP addresses (e.g. tcp://192.168.1.50:2375).
            </p>
          </div>

          <div>
            <label htmlFor="host-exclude" className="block text-xs font-medium text-slate-300 mb-1">
              Excluded Containers
            </label>
            <input
              id="host-exclude"
              type="text"
              placeholder="gluetun, qbittorrent"
              value={excludeContainers}
              onChange={(e) => setExcludeContainers(e.target.value)}
              className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
            <p className="text-[11px] text-slate-500 mt-1">
              Comma-separated list of container names or IDs to exclude from ingestion.
            </p>
          </div>

          <div className="flex items-center gap-4 pt-1">
            <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
              />
              <span>Enable container collection</span>
            </label>

            <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={tlsEnabled}
                onChange={(e) => setTlsEnabled(e.target.checked)}
                className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
              />
              <span>Use Mutual TLS (mTLS)</span>
            </label>
          </div>

          {tlsEnabled && (
            <div className="space-y-3 p-3.5 bg-dark-950 border border-dark-750 rounded-lg">
              <div className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
                <Lock className="w-3.5 h-3.5 text-accent-400" />
                <span>TLS Credentials (PEM format)</span>
              </div>

              <div>
                <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={verifyHostname}
                    onChange={(e) => setVerifyHostname(e.target.checked)}
                    className="rounded bg-dark-950 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
                  />
                  <span>Verify TLS host name</span>
                </label>
                <p className="text-[10px] text-slate-500 mt-1">
                  Turn off only for daemons reached by IP whose certificate does not list that IP.
                </p>
              </div>

              <div>
                <label htmlFor="tls-ca" className="block text-[11px] font-medium text-slate-400 mb-1">
                  CA Certificate
                </label>
                <textarea
                  id="tls-ca"
                  rows={2}
                  placeholder="-----BEGIN CERTIFICATE-----"
                  value={caCert}
                  onChange={(e) => setCaCert(e.target.value)}
                  className="w-full bg-dark-900 border border-dark-700 rounded p-2 text-[11px] font-mono text-slate-200 placeholder-slate-600 focus:outline-hidden focus:border-accent-500 resize-none"
                />
              </div>

              <div>
                <label htmlFor="tls-cert" className="block text-[11px] font-medium text-slate-400 mb-1">
                  Client Certificate
                </label>
                <textarea
                  id="tls-cert"
                  rows={2}
                  placeholder="-----BEGIN CERTIFICATE-----"
                  value={clientCert}
                  onChange={(e) => setClientCert(e.target.value)}
                  className="w-full bg-dark-900 border border-dark-700 rounded p-2 text-[11px] font-mono text-slate-200 placeholder-slate-600 focus:outline-hidden focus:border-accent-500 resize-none"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label htmlFor="tls-key" className="block text-[11px] font-medium text-slate-400">
                    Client Private Key
                  </label>
                  {editingHost?.has_client_key && (
                    <button
                      type="button"
                      onClick={() => {
                        setClearClientKey((prev) => !prev);
                        if (!clearClientKey) setClientKey('');
                      }}
                      className={`text-[11px] transition cursor-pointer ${
                        clearClientKey ? 'text-amber-400 font-semibold' : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {clearClientKey ? 'Key marked for removal (Undo)' : 'Remove stored key'}
                    </button>
                  )}
                </div>
                <textarea
                  id="tls-key"
                  rows={2}
                  disabled={clearClientKey}
                  placeholder={
                    clearClientKey
                      ? 'Saved key will be removed on save.'
                      : editingHost?.has_client_key
                      ? 'Key is saved. Leave blank to retain existing key.'
                      : '-----BEGIN EC PRIVATE KEY-----'
                  }
                  value={clientKey}
                  onChange={(e) => setClientKey(e.target.value)}
                  className="w-full bg-dark-900 border border-dark-700 rounded p-2 text-[11px] font-mono text-slate-200 placeholder-slate-600 focus:outline-hidden focus:border-accent-500 resize-none disabled:opacity-50"
                />
                <p className="text-[10px] text-slate-500 mt-1">
                  Encrypted at rest in database using application master key. Decrypted to memory only during connection setup.
                </p>
              </div>
            </div>
          )}

          {modalTestResult && (
            <div
              className={`p-3 rounded-lg border text-xs flex items-center gap-2 ${
                modalTestResult.success
                  ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
                  : 'bg-red-950/60 border-red-800 text-red-300'
              }`}
            >
              {modalTestResult.success ? (
                <>
                  <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                  <span>
                    Connection verified successfully. Version: {modalTestResult.version ?? 'Docker'} ({Math.round(modalTestResult.latency_ms ?? 0)} ms).
                  </span>
                </>
              ) : (
                <>
                  <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                  <span>{modalTestResult.error || 'Connection failed.'}</span>
                </>
              )}
            </div>
          )}

          <div className="flex items-center justify-between pt-3 border-t border-dark-700">
            <button
              type="button"
              onClick={handleTestModal}
              disabled={modalTestLoading || !url.trim()}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-300 bg-dark-800 hover:bg-dark-700 border border-dark-600 rounded-lg transition cursor-pointer disabled:opacity-50"
            >
              <Play className="w-3.5 h-3.5 text-accent-400" />
              <span>{modalTestLoading ? 'Testing...' : 'Test Connection'}</span>
            </button>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleCloseModal}
                className="px-3 py-1.5 text-xs font-medium text-slate-400 hover:text-slate-200 transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isSubmitting || !name.trim() || !url.trim()}
                className="px-4 py-1.5 text-xs font-medium text-white bg-accent-600 hover:bg-accent-500 rounded-lg shadow-xs transition cursor-pointer disabled:opacity-50"
              >
                {isSubmitting ? 'Saving...' : editingHost ? 'Update Host' : 'Add Host'}
              </button>
            </div>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={Boolean(hostToDelete)}
        onClose={() => setHostToDelete(null)}
        title="Remove Docker Host"
        maxWidth="max-w-md"
      >
        <div className="space-y-4">
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

          <div className="flex items-center justify-end gap-2 pt-2 border-t border-dark-700">
            <button
              type="button"
              onClick={() => setHostToDelete(null)}
              disabled={isDeleting}
              className="px-3 py-1.5 text-xs font-medium text-slate-400 hover:text-slate-200 transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleDeleteConfirm}
              disabled={isDeleting}
              className="px-3.5 py-1.5 text-xs font-medium text-white bg-red-600 hover:bg-red-500 rounded-lg transition cursor-pointer disabled:opacity-50"
            >
              {isDeleting ? 'Removing...' : 'Remove Host'}
            </button>
          </div>
        </div>
      </Modal>

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
                <div className="p-2.5 rounded bg-dark-950/80 border border-dark-700 font-mono text-[11px] text-slate-300 space-y-1">
                  <div>
                    <span className="text-slate-500">Host / Alias:</span> {deletedHostNotice.name}
                  </div>
                  <div>
                    <span className="text-slate-500">Source IP:</span> {deletedHostNotice.ip}
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
                  openAddModal();
                  setName(targetInfo.name);
                  setSourceAlias(targetInfo.name);
                  setIsAliasManuallyEdited(true);
                  if (targetInfo.ip && targetInfo.ip !== 'docker') {
                    setUrl(`tcp://${targetInfo.ip}:2375`);
                  } else {
                    setUrl('unix:///var/run/docker.sock');
                  }
                }}
                className="px-3.5 py-1.5 text-xs bg-accent-600 hover:bg-accent-500 text-white font-medium rounded-lg shadow transition cursor-pointer"
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
