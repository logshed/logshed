import React, { useState } from 'react';
import { AlertCircle, CheckCircle2, Lock, Play, XCircle } from 'lucide-react';
import { DockerEndpoint, DockerEndpointCreate, DockerEndpointUpdate, DockerEndpointTestResult } from '../../types.ts';
import { createDockerEndpoint, updateDockerEndpoint, testDockerEndpoint } from '../../api/dockerHosts.ts';
import { Modal } from '../common/Modal.tsx';
import { HOST_ALIAS_MAX_LENGTH, NAME_MAX_LENGTH } from '../../utils/fieldLimits.ts';

export interface DockerHostFormModalProps {
  /** The host to edit, or null to add one. */
  editingHost: DockerEndpoint | null;
  /** Starting values for a new host (from a container that asked to be added). */
  initialAddHost?: { name?: string; url?: string; source_alias?: string } | null;
  onClose: () => void;
  /** Runs after a save: the dialog is closed by then. */
  onSaved: () => Promise<void>;
}

/**
 * Adds or edits a Docker host: its name, Engine API address, source alias, excluded containers and
 * TLS settings, with a connection test. Mount it only while it is open; a new key starts it afresh.
 */
export const DockerHostFormModal: React.FC<DockerHostFormModalProps> = ({
  editingHost,
  initialAddHost,
  onClose,
  onSaved,
}) => {
  const [name, setName] = useState<string>(editingHost?.name ?? initialAddHost?.name ?? '');
  const [url, setUrl] = useState<string>(editingHost?.url ?? initialAddHost?.url ?? '');
  const [sourceAlias, setSourceAlias] = useState<string>(
    editingHost ? editingHost.source_alias || 'docker' : initialAddHost?.source_alias || 'docker'
  );
  const [isAliasManuallyEdited, setIsAliasManuallyEdited] = useState<boolean>(
    Boolean(editingHost || initialAddHost?.source_alias)
  );
  const [excludeContainers, setExcludeContainers] = useState<string>(editingHost?.exclude_containers || '');
  const [enabled, setEnabled] = useState<boolean>(editingHost ? editingHost.enabled : true);
  const [tlsEnabled, setTlsEnabled] = useState<boolean>(editingHost ? editingHost.tls_enabled : false);
  const [verifyHostname, setVerifyHostname] = useState<boolean>(editingHost?.verify_hostname ?? true);
  const [caCert, setCaCert] = useState<string>(editingHost?.ca_cert || '');
  const [clientCert, setClientCert] = useState<string>(editingHost?.client_cert || '');
  const [clientKey, setClientKey] = useState<string>('');
  const [clearClientKey, setClearClientKey] = useState<boolean>(false);

  const [modalTestLoading, setModalTestLoading] = useState<boolean>(false);
  const [modalTestResult, setModalTestResult] = useState<DockerEndpointTestResult | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [modalError, setModalError] = useState<string | null>(null);

  const handleCloseModal = onClose;

  const slugifyAlias = (val: string): string => {
    return val
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64);
  };

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
      await onSaved();
    } catch (err: any) {
      setModalError(err.message || 'Failed to save Docker host.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      isOpen
      onClose={handleCloseModal}
      title={editingHost ? 'Edit Docker Host' : 'Add Docker Host'}
      maxWidth="max-w-2xl"
    >
      <form onSubmit={handleSubmitModal} className="space-y-4">
        {modalError && (
          <div className="p-3 rounded-lg border bg-danger-950/60 border-danger-800 text-danger-300 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-danger-400 shrink-0" />
            <span>{modalError}</span>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label htmlFor="host-name" className="block text-xs font-medium text-fg-soft mb-1">
              Host Name <span className="text-danger-400">*</span>
            </label>
            <input
              id="host-name"
              type="text"
              placeholder="e.g. Unraid Server"
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              required
              maxLength={NAME_MAX_LENGTH}
              disabled={editingHost?.id === 1}
              className={`w-full bg-canvas border border-line rounded-lg px-3 py-2 text-xs text-fg-strong placeholder-fg-subtle focus:outline-hidden focus:border-accent-500 ${
                editingHost?.id === 1 ? 'opacity-60 cursor-not-allowed' : ''
              }`}
            />
            {editingHost?.id === 1 && (
              <p className="text-[11px] text-fg-muted mt-1">
                Primary Docker endpoint name is fixed.
              </p>
            )}
          </div>

          <div>
            <label htmlFor="host-alias" className="block text-xs font-medium text-fg-soft mb-1">
              Host Alias <span className="text-danger-400">*</span>
            </label>
            <input
              id="host-alias"
              type="text"
              placeholder="docker"
              value={sourceAlias}
              onChange={(e) => handleAliasChange(e.target.value)}
              required
              maxLength={HOST_ALIAS_MAX_LENGTH}
              className="w-full bg-canvas border border-line rounded-lg px-3 py-2 text-xs text-fg-strong placeholder-fg-subtle focus:outline-hidden focus:border-accent-500 font-mono"
            />
            <p className="text-[11px] text-fg-muted mt-1">
              Label assigned to logs under the Host / IP column (defaults to host name).
            </p>
          </div>
        </div>

        <div>
          <label htmlFor="host-url" className="block text-xs font-medium text-fg-soft mb-1">
            Docker Endpoint URL <span className="text-danger-400">*</span>
          </label>
          <input
            id="host-url"
            type="text"
            placeholder="unix:///var/run/docker.sock or tcp://192.168.1.50:2375"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
            className="w-full bg-canvas border border-line rounded-lg px-3 py-2 text-xs text-fg-strong placeholder-fg-subtle focus:outline-hidden focus:border-accent-500 font-mono"
          />
          <p className="text-[11px] text-fg-muted mt-1">
            Supports local socket paths or remote TCP addresses (e.g. tcp://192.168.1.50:2375).
          </p>
        </div>

        <div>
          <label htmlFor="host-exclude" className="block text-xs font-medium text-fg-soft mb-1">
            Excluded Containers
          </label>
          <input
            id="host-exclude"
            type="text"
            placeholder="gluetun, qbittorrent"
            value={excludeContainers}
            onChange={(e) => setExcludeContainers(e.target.value)}
            className="w-full bg-canvas border border-line rounded-lg px-3 py-2 text-xs text-fg-strong placeholder-fg-subtle focus:outline-hidden focus:border-accent-500 font-mono"
          />
          <p className="text-[11px] text-fg-muted mt-1">
            Comma-separated list of container names or IDs to exclude from ingestion.
          </p>
        </div>

        <div className="flex items-center gap-4 pt-1">
          <label className="flex items-center gap-2 text-xs text-fg-soft cursor-pointer">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="rounded-sm bg-canvas border-line text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
            />
            <span>Enable container collection</span>
          </label>

          <label className="flex items-center gap-2 text-xs text-fg-soft cursor-pointer">
            <input
              type="checkbox"
              checked={tlsEnabled}
              onChange={(e) => setTlsEnabled(e.target.checked)}
              className="rounded-sm bg-canvas border-line text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
            />
            <span>Use Mutual TLS (mTLS)</span>
          </label>
        </div>

        {tlsEnabled && (
          <div className="space-y-3 p-3.5 bg-canvas border border-line-muted rounded-lg">
            <div className="flex items-center gap-1.5 text-xs font-medium text-fg-soft">
              <Lock className="w-3.5 h-3.5 text-accent-400" />
              <span>TLS Credentials (PEM format)</span>
            </div>

            <div>
              <label className="flex items-center gap-2 text-xs text-fg-soft cursor-pointer">
                <input
                  type="checkbox"
                  checked={verifyHostname}
                  onChange={(e) => setVerifyHostname(e.target.checked)}
                  className="rounded-sm bg-canvas border-line text-accent-600 focus:ring-0 focus:ring-offset-0 w-4 h-4 cursor-pointer"
                />
                <span>Verify TLS host name</span>
              </label>
              <p className="text-[11px] text-fg-muted mt-1">
                Turn off only for daemons reached by IP whose certificate does not list that IP.
              </p>
            </div>

            <div>
              <label htmlFor="tls-ca" className="block text-[11px] font-medium text-fg-muted mb-1">
                CA Certificate
              </label>
              <textarea
                id="tls-ca"
                rows={2}
                placeholder="-----BEGIN CERTIFICATE-----"
                value={caCert}
                onChange={(e) => setCaCert(e.target.value)}
                className="w-full bg-surface border border-line rounded-sm p-2 text-[11px] font-mono text-fg placeholder-fg-faint focus:outline-hidden focus:border-accent-500 resize-none"
              />
            </div>

            <div>
              <label htmlFor="tls-cert" className="block text-[11px] font-medium text-fg-muted mb-1">
                Client Certificate
              </label>
              <textarea
                id="tls-cert"
                rows={2}
                placeholder="-----BEGIN CERTIFICATE-----"
                value={clientCert}
                onChange={(e) => setClientCert(e.target.value)}
                className="w-full bg-surface border border-line rounded-sm p-2 text-[11px] font-mono text-fg placeholder-fg-faint focus:outline-hidden focus:border-accent-500 resize-none"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <label htmlFor="tls-key" className="block text-[11px] font-medium text-fg-muted">
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
                      clearClientKey ? 'text-warning-400 font-semibold' : 'text-fg-muted hover:text-fg'
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
                className="w-full bg-surface border border-line rounded-sm p-2 text-[11px] font-mono text-fg placeholder-fg-faint focus:outline-hidden focus:border-accent-500 resize-none disabled:opacity-50"
              />
              <p className="text-[11px] text-fg-muted mt-1">
                Encrypted at rest in database using application master key. Decrypted to memory only during connection setup.
              </p>
            </div>
          </div>
        )}

        {modalTestResult && (
          <div
            className={`p-3 rounded-lg border text-xs flex items-center gap-2 ${
              modalTestResult.success
                ? 'bg-success-950/60 border-success-800 text-success-300'
                : 'bg-danger-950/60 border-danger-800 text-danger-300'
            }`}
          >
            {modalTestResult.success ? (
              <>
                <CheckCircle2 className="w-4 h-4 text-success-400 shrink-0" />
                <span>
                  Connection verified successfully. Version: {modalTestResult.version ?? 'Docker'} ({Math.round(modalTestResult.latency_ms ?? 0)} ms).
                </span>
              </>
            ) : (
              <>
                <XCircle className="w-4 h-4 text-danger-400 shrink-0" />
                <span>{modalTestResult.error || 'Connection failed.'}</span>
              </>
            )}
          </div>
        )}

        <div className="flex items-center justify-between pt-3 border-t border-line">
          <button
            type="button"
            onClick={handleTestModal}
            disabled={modalTestLoading || !url.trim()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-fg-soft bg-raised hover:bg-raised-strong border border-line-strong rounded-lg transition cursor-pointer disabled:opacity-50"
          >
            <Play className="w-3.5 h-3.5 text-accent-400" />
            <span>{modalTestLoading ? 'Testing...' : 'Test Connection'}</span>
          </button>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleCloseModal}
              className="px-3 py-1.5 text-xs font-medium text-fg-muted hover:text-fg transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !name.trim() || !url.trim()}
              className="px-4 py-1.5 text-xs font-medium text-on-fill bg-accent-700 hover:bg-accent-600 rounded-lg shadow-xs transition cursor-pointer disabled:opacity-50"
            >
              {isSubmitting ? 'Saving...' : editingHost ? 'Update Host' : 'Add Host'}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
};
