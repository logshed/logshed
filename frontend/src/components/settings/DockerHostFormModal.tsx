import React, { useState } from 'react';
import { AlertCircle, CheckCircle2, Lock, Play, XCircle } from 'lucide-react';
import { DockerEndpoint, DockerEndpointCreate, DockerEndpointUpdate, DockerEndpointTestResult } from '../../types.ts';
import { createDockerEndpoint, updateDockerEndpoint, testDockerEndpoint } from '../../api/dockerHosts.ts';
import { Modal } from '../common/Modal.tsx';
import { HOST_ALIAS_MAX_LENGTH, NAME_MAX_LENGTH } from '../../utils/fieldLimits.ts';
import { Button } from '../common/Button.tsx';
import { Checkbox, LABEL_TEXT_CLASS, REQUIRED_MARK, TextArea, TextField } from '../common/Field.tsx';

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
          <TextField
            label={<>Host Name {REQUIRED_MARK}</>}
            hint={editingHost?.id === 1 ? 'Primary Docker endpoint name is fixed.' : undefined}
            id="host-name"
            type="text"
            placeholder="e.g. Unraid Server"
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            required
            maxLength={NAME_MAX_LENGTH}
            disabled={editingHost?.id === 1}
          />

          <TextField
            label={<>Host Alias {REQUIRED_MARK}</>}
            hint="Label assigned to logs under the Host / IP column (defaults to host name)."
            id="host-alias"
            type="text"
            placeholder="docker"
            value={sourceAlias}
            onChange={(e) => handleAliasChange(e.target.value)}
            required
            maxLength={HOST_ALIAS_MAX_LENGTH}
            mono
          />
        </div>

        <TextField
          label={<>Docker Endpoint URL {REQUIRED_MARK}</>}
          hint="Supports local socket paths or remote TCP addresses (e.g. tcp://192.168.1.50:2375)."
          id="host-url"
          type="text"
          placeholder="unix:///var/run/docker.sock or tcp://192.168.1.50:2375"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
          mono
        />

        <TextField
          label="Excluded Containers"
          hint="Comma-separated list of container names or IDs to exclude from ingestion."
          id="host-exclude"
          type="text"
          placeholder="gluetun, qbittorrent"
          value={excludeContainers}
          onChange={(e) => setExcludeContainers(e.target.value)}
          mono
        />

        <div className="flex flex-wrap items-center gap-4 pt-1">
          <Checkbox label="Enable container collection" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <Checkbox label="Use Mutual TLS (mTLS)" checked={tlsEnabled} onChange={(e) => setTlsEnabled(e.target.checked)} />
        </div>

        {tlsEnabled && (
          <div className="space-y-3 p-3.5 bg-canvas border border-line-subtle rounded-lg">
            <div className="flex items-center gap-1.5 text-xs font-medium text-fg-soft">
              <Lock className="w-3.5 h-3.5 text-accent-400" />
              <span>TLS Credentials (PEM format)</span>
            </div>

            <div>
              <Checkbox
                label="Verify TLS host name"
                checked={verifyHostname}
                onChange={(e) => setVerifyHostname(e.target.checked)}
              />
              <p className="text-[11px] text-fg-muted mt-1 pl-6">
                Turn off only for daemons reached by IP whose certificate does not list that IP.
              </p>
            </div>

            <TextArea
              label="CA Certificate"
              id="tls-ca"
              rows={2}
              placeholder="-----BEGIN CERTIFICATE-----"
              value={caCert}
              onChange={(e) => setCaCert(e.target.value)}
              mono
            />

            <TextArea
              label="Client Certificate"
              id="tls-cert"
              rows={2}
              placeholder="-----BEGIN CERTIFICATE-----"
              value={clientCert}
              onChange={(e) => setClientCert(e.target.value)}
              mono
            />

            <div>
              <div className="flex items-center justify-between mb-1">
                <label htmlFor="tls-key" className={LABEL_TEXT_CLASS}>
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
              <TextArea
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
                mono
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
          <Button
            onClick={handleTestModal}
            disabled={modalTestLoading || !url.trim()}
            icon={<Play className="w-3.5 h-3.5 text-accent-400" />}
          >
            {modalTestLoading ? 'Testing...' : 'Test Connection'}
          </Button>

          <div className="flex items-center gap-2">
            <Button onClick={handleCloseModal}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={isSubmitting || !name.trim() || !url.trim()}>
              {isSubmitting ? 'Saving...' : editingHost ? 'Update Host' : 'Add Host'}
            </Button>
          </div>
        </div>
      </form>
    </Modal>
  );
};
