import React, { useEffect, useRef, useState } from 'react';
import { Server, Plus, Trash2, Edit2, AlertCircle, Loader2, ScrollText } from 'lucide-react';
import { HostAlias } from '../../types.ts';
import { fetchAliases, saveAlias, deleteAlias } from '../../api/aliases.ts';
import { useMediaQuery } from '../../utils/hooks.ts';
import { Modal } from '../common/Modal.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { useAlias } from '../../context/AliasContext.tsx';
import { Button, IconButton } from '../common/Button.tsx';
import { Card, CardHeader, SECTION_TITLE_CLASS } from '../common/Card.tsx';
import { TextField } from '../common/Field.tsx';
import { TABLE_HEAD_ROW } from '../common/Table.tsx';

interface HostAliasManagerProps {
  initialAddIp?: string | null;
  onAliasSaved?: () => void;
  /** Called when the Add / Edit modal is dismissed without saving, so a hand-off can be cleared. */
  onCancel?: () => void;
}

/** A mapping's row actions: its logs in the Console, edit and delete. */
const AliasRowActions: React.FC<{
  item: HostAlias;
  isDeleting: boolean;
  onViewLogs: (item: HostAlias) => void;
  onEdit: (item: HostAlias) => void;
  onDelete: (ip: string) => void;
}> = ({ item, isDeleting, onViewLogs, onEdit, onDelete }) => (
  <>
    <Button
      size="sm"
      variant="ghost"
      onClick={() => onViewLogs(item)}
      title={`View logs for ${item.alias}`}
      aria-label={`View logs for ${item.alias}`}
      icon={<ScrollText className="w-3.5 h-3.5" />}
    >
      View logs
    </Button>
    <IconButton size="sm" onClick={() => onEdit(item)} title="Edit Alias" label={`Edit alias ${item.alias}`}>
      <Edit2 className="w-3.5 h-3.5" />
    </IconButton>
    <IconButton
      size="sm"
      variant="danger"
      disabled={isDeleting}
      onClick={() => onDelete(item.ip)}
      title="Delete Alias"
      label={`Delete alias ${item.alias}`}
    >
      <Trash2 className="w-3.5 h-3.5" />
    </IconButton>
  </>
);

export const HostAliasManager: React.FC<HostAliasManagerProps> = ({
  initialAddIp,
  onAliasSaved,
  onCancel,
}) => {
  const { bumpAliasVersion } = useAlias();
  const [aliases, setAliases] = useState<HostAlias[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Add / Edit Modal state
  const [isModalOpen, setIsModalOpen] = useState<boolean>(false);
  const [editingIp, setEditingIp] = useState<string | null>(null);
  const [ip, setIp] = useState<string>('');
  const [alias, setAlias] = useState<string>('');
  const [notes, setNotes] = useState<string>('');
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [modalError, setModalError] = useState<string | null>(null);

  // Delete Modal state
  const [isDeletingIp, setIsDeletingIp] = useState<string | null>(null);
  const [deleteTargetIp, setDeleteTargetIp] = useState<string | null>(null);

  // A hand-off IP the user already dismissed must not reopen the modal when the alias list reloads.
  const dismissedAddIpRef = useRef<string | null>(null);

  const loadAliases = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);
      const list = await fetchAliases();
      setAliases(list);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load host aliases.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadAliases();
  }, []);

  useEffect(() => {
    if (initialAddIp && initialAddIp !== dismissedAddIpRef.current) {
      const existing = aliases.find((a) => a.ip === initialAddIp);
      if (existing) {
        setEditingIp(existing.ip);
        setIp(existing.ip);
        setAlias(existing.alias);
        setNotes(existing.notes || '');
      } else {
        setEditingIp(null);
        setIp(initialAddIp);
        setAlias('');
        setNotes('');
      }
      setModalError(null);
      setIsModalOpen(true);
    } else if (!initialAddIp) {
      dismissedAddIpRef.current = null;
    }
  }, [initialAddIp, aliases]);

  const openAddModal = () => {
    setEditingIp(null);
    setIp('');
    setAlias('');
    setNotes('');
    setModalError(null);
    setIsModalOpen(true);
  };

  const openEditModal = (item: HostAlias) => {
    setEditingIp(item.ip);
    setIp(item.ip);
    setAlias(item.alias);
    setNotes(item.notes || '');
    setModalError(null);
    setIsModalOpen(true);
  };

  /** Opens the Console filtered to this alias (same URL hand-off the Navbar uses). */
  const handleViewLogs = (item: HostAlias) => {
    window.history.pushState(null, '', `/?source=${encodeURIComponent(item.alias)}`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setEditingIp(null);
    setIp('');
    setAlias('');
    setNotes('');
    setModalError(null);
  };

  const handleCancelModal = () => {
    dismissedAddIpRef.current = initialAddIp || null;
    handleCloseModal();
    if (onCancel) onCancel();
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ip.trim() || !alias.trim()) return;

    try {
      setIsSaving(true);
      setModalError(null);
      await saveAlias({
        ip: ip.trim(),
        alias: alias.trim(),
        notes: notes.trim() || null,
      });
      handleCloseModal();
      await loadAliases();
      bumpAliasVersion();
      if (onAliasSaved) onAliasSaved();
    } catch (err: any) {
      setModalError(err.message || 'Failed to save host alias mapping.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!deleteTargetIp) return;
    const targetIp = deleteTargetIp;
    try {
      setIsDeletingIp(targetIp);
      await deleteAlias(targetIp);
      setDeleteTargetIp(null);
      await loadAliases();
      bumpAliasVersion();
      if (onAliasSaved) onAliasSaved();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to delete host alias.');
      setDeleteTargetIp(null);
    } finally {
      setIsDeletingIp(null);
    }
  };

  const isMobile = useMediaQuery('(max-width: 767px)');

  return (
    <div className="space-y-6">
      {errorMsg && (
        <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
          <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Syslog Host Aliases Card */}
      <Card padding="flush">
        <CardHeader
          className="p-3.5 sm:p-5 border-b border-line"
          icon={<Server />}
          title="Syslog Host Aliases"
          description="Map incoming syslog source IP addresses to friendly host names (e.g. 192.168.1.1 to OPNsense Firewall). Configured syslog senders push logs directly to LogShed and un-aliased hosts appear by IP in the console view."
          actions={
            <Button variant="primary" onClick={openAddModal} icon={<Plus className="w-3.5 h-3.5" />}>
              Add Host Alias
            </Button>
          }
        />

        {/* Active Host Mappings Sub-Section */}
        <div className="p-3.5 sm:px-5 sm:py-3 bg-canvas/40 border-b border-line-subtle flex items-center justify-between">
          <h4 className={SECTION_TITLE_CLASS}>Active Host Mappings ({aliases.length})</h4>
        </div>

        {isLoading ? (
          <div className="p-6 text-center text-fg-muted font-mono text-xs">Loading mappings...</div>
        ) : aliases.length === 0 ? (
          <div className="p-6 text-center text-fg-muted font-mono text-xs">
            No host aliases mapped yet. Add a mapping to label incoming syslog IP addresses.
          </div>
        ) : isMobile ? (
          /* Mobile Card View */
          <div className="divide-y divide-line-subtle">
            {aliases.map((item) => (
              <div key={item.ip} className="p-3.5 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-accent-400 font-semibold text-xs sm:text-sm">{item.ip}</span>
                  <div className="flex items-center gap-1.5">
                    <AliasRowActions
                      item={item}
                      isDeleting={isDeletingIp === item.ip}
                      onViewLogs={handleViewLogs}
                      onEdit={openEditModal}
                      onDelete={setDeleteTargetIp}
                    />
                  </div>
                </div>
                <div className="text-fg font-mono text-xs font-medium">{item.alias}</div>
                {item.notes && (
                  <div className="text-fg-muted text-xs">
                    {item.notes}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          /* Desktop Table View */
          <div className="divide-y divide-line-subtle text-xs">
            <div className={`grid grid-cols-[160px_200px_1fr_160px] px-4 select-none ${TABLE_HEAD_ROW}`}>
              <div className="py-2.5">Source IP</div>
              <div className="py-2.5">Friendly Alias</div>
              <div className="py-2.5">Notes</div>
              <div className="py-2.5 text-right">Actions</div>
            </div>

            {aliases.map((item) => (
              <div
                key={item.ip}
                className="grid grid-cols-[160px_200px_1fr_160px] px-4 py-2.5 items-center hover:bg-raised/50 transition"
              >
                <div className="font-mono text-accent-400 font-semibold">{item.ip}</div>
                <div className="font-mono text-fg">{item.alias}</div>
                <div className="text-fg-muted truncate pr-2">
                  {item.notes || '-'}
                </div>
                <div className="flex items-center justify-end gap-1.5">
                  <AliasRowActions
                    item={item}
                    isDeleting={isDeletingIp === item.ip}
                    onViewLogs={handleViewLogs}
                    onEdit={openEditModal}
                    onDelete={setDeleteTargetIp}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Add / Edit Host Alias Modal */}
      {isModalOpen && (
        <Modal
          isOpen={isModalOpen}
          onClose={handleCancelModal}
          title={editingIp ? `Edit Host Alias - ${editingIp}` : 'Add Host Alias'}
          maxWidth="max-w-md"
        >
          <form onSubmit={handleSave} className="space-y-4">
            {modalError && (
              <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
                <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
                <span>{modalError}</span>
              </div>
            )}

            <TextField
              label="Source IP Address"
              hint={editingIp ? 'Source IP cannot be modified while editing. Delete and re-create to change the IP.' : undefined}
              type="text"
              value={ip}
              onChange={(e) => setIp(e.target.value)}
              disabled={Boolean(editingIp)}
              placeholder="e.g. 192.168.1.50"
              required
              mono
            />

            <TextField
              label="Friendly Host Alias"
              type="text"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="e.g. Proxmox-Node-01"
              required
              mono
            />

            <TextField
              label="Notes (Optional)"
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Main hypervisor in rack 1"
            />

            <div className="flex items-center justify-end gap-2 pt-3 border-t border-line-subtle">
              <Button onClick={handleCancelModal}>Cancel</Button>
              <Button
                type="submit"
                variant="primary"
                disabled={isSaving || !ip.trim() || !alias.trim()}
                icon={isSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : undefined}
              >
                {isSaving ? 'Saving...' : editingIp ? 'Update Mapping' : 'Add Mapping'}
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* Delete Host Alias Confirmation Modal */}
      {deleteTargetIp && (
        <ConfirmDialog
          isOpen={!!deleteTargetIp}
          title="Delete Host Alias"
          look="notice"
          onCancel={() => setDeleteTargetIp(null)}
          onConfirm={handleConfirmDelete}
          confirmDisabled={isDeletingIp === deleteTargetIp}
          confirmIcon={<Trash2 className="w-3.5 h-3.5" />}
          confirmLabel={isDeletingIp === deleteTargetIp ? 'Deleting...' : 'Delete Alias'}
        >
          <p className="font-semibold text-fg-strong text-xs">
            Remove host alias mapping?
          </p>
          <p className="text-fg-muted leading-relaxed">
            Incoming logs from this IP address will no longer be assigned a friendly alias.
          </p>
          <div className="p-2.5 rounded-sm bg-canvas/80 border border-line font-mono text-[11px] text-fg-soft space-y-1">
            <div>
              <span className="text-fg-muted">IP:</span> {deleteTargetIp}
            </div>
            {aliases.find((a) => a.ip === deleteTargetIp)?.alias && (
              <div>
                <span className="text-fg-muted">Alias:</span>{' '}
                {aliases.find((a) => a.ip === deleteTargetIp)?.alias}
              </div>
            )}
          </div>
        </ConfirmDialog>
      )}
    </div>
  );
};
