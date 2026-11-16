import React, { useState } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import { saveAlias } from '../../api/aliases.ts';
import { Modal } from '../common/Modal.tsx';
import { Button } from '../common/Button.tsx';
import { TextField } from '../common/Field.tsx';

export interface HostAliasFormValues {
  ip: string;
  alias?: string;
  notes?: string | null;
}

export interface HostAliasFormModalProps {
  /** Starting values: the address (and, when editing, the alias and notes it has). */
  initial: HostAliasFormValues;
  /** Editing an existing alias: the address cannot change. */
  editing: boolean;
  /** A problem to show when the dialog opens (the alias's notes could not be read). */
  initialError?: string | null;
  onClose: () => void;
  /** Runs after a save, once the dialog has closed. */
  onSaved: () => void | Promise<void>;
}

/**
 * Adds or edits a host alias (an address's friendly name), in Settings > Sources and over the log
 * drawer. Mount it only while it is open; a new key starts it afresh.
 */
export const HostAliasFormModal: React.FC<HostAliasFormModalProps> = ({ initial, editing, initialError = null, onClose, onSaved }) => {
  const [ip, setIp] = useState(initial.ip);
  const [alias, setAlias] = useState(initial.alias ?? '');
  const [notes, setNotes] = useState(initial.notes ?? '');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ip.trim() || !alias.trim()) return;
    try {
      setIsSaving(true);
      setError(null);
      await saveAlias({ ip: ip.trim(), alias: alias.trim(), notes: notes.trim() || null });
      onClose();
      await onSaved();
    } catch (err: any) {
      setError(err.message || 'Failed to save host alias mapping.');
      setIsSaving(false);
    }
  };

  return (
    <Modal isOpen onClose={onClose} title={editing ? `Edit Host Alias - ${initial.ip}` : 'Add Host Alias'} maxWidth="max-w-md">
      <form onSubmit={handleSave} className="space-y-4">
        {error && (
          <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
            <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        <TextField
          label="Source IP Address"
          hint={editing ? 'Source IP cannot be modified while editing. Delete and re-create to change the IP.' : undefined}
          type="text"
          value={ip}
          onChange={(e) => setIp(e.target.value)}
          disabled={editing}
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
          <Button onClick={onClose}>Cancel</Button>
          <Button
            type="submit"
            variant="primary"
            disabled={isSaving || !ip.trim() || !alias.trim()}
            icon={isSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : undefined}
          >
            {isSaving ? 'Saving...' : editing ? 'Update Mapping' : 'Add Mapping'}
          </Button>
        </div>
      </form>
    </Modal>
  );
};
