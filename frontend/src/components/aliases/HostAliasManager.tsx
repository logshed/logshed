import React, { useEffect, useState } from 'react';
import { Server, Plus, Trash2, Edit2, Check, AlertCircle, AlertTriangle } from 'lucide-react';
import { HostAlias } from '../../types.ts';
import { fetchAliases, saveAlias, deleteAlias } from '../../api/aliases.ts';
import { useMediaQuery } from '../../utils/hooks.ts';
import { Modal } from '../common/Modal.tsx';

interface HostAliasManagerProps {
  initialAddIp?: string | null;
  onAliasSaved?: () => void;
}

export const HostAliasManager: React.FC<HostAliasManagerProps> = ({
  initialAddIp,
  onAliasSaved,
}) => {
  const [aliases, setAliases] = useState<HostAlias[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [ip, setIp] = useState<string>(initialAddIp || '');
  const [alias, setAlias] = useState<string>('');
  const [notes, setNotes] = useState<string>('');
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [editingIp, setEditingIp] = useState<string | null>(null);
  const [isDeletingIp, setIsDeletingIp] = useState<string | null>(null);
  const [deleteTargetIp, setDeleteTargetIp] = useState<string | null>(null);

  useEffect(() => {
    if (initialAddIp) {
      setIp(initialAddIp);
      const existing = aliases.find((a) => a.ip === initialAddIp);
      if (existing) {
        setEditingIp(existing.ip);
        setAlias(existing.alias);
        setNotes(existing.notes || '');
      }
    }
  }, [initialAddIp, aliases]);

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

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ip.trim() || !alias.trim()) return;

    try {
      setIsSaving(true);
      setErrorMsg(null);
      await saveAlias({
        ip: ip.trim(),
        alias: alias.trim(),
        notes: notes.trim() || null,
      });
      setIp('');
      setAlias('');
      setNotes('');
      setEditingIp(null);
      await loadAliases();
      if (onAliasSaved) onAliasSaved();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to save host alias mapping.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleEdit = (item: HostAlias) => {
    setEditingIp(item.ip);
    setIp(item.ip);
    setAlias(item.alias);
    setNotes(item.notes || '');
  };

  const handleCancelEdit = () => {
    setEditingIp(null);
    setIp('');
    setAlias('');
    setNotes('');
  };

  const handleConfirmDelete = async () => {
    if (!deleteTargetIp) return;
    const targetIp = deleteTargetIp;
    try {
      setIsDeletingIp(targetIp);
      await deleteAlias(targetIp);
      setDeleteTargetIp(null);
      await loadAliases();
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
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Add / Edit Form Card */}
      <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Server className="w-4 h-4 text-accent-500" />
              <span>Host Alias Manager</span>
            </h3>
            <p className="text-[11px] text-slate-400 mt-1">
              {editingIp
                ? `Editing host mapping for ${editingIp}.`
                : 'Map incoming source IP addresses to friendly host names (e.g. 192.168.1.1 to OPNsense Firewall).'}
            </p>
          </div>
          {editingIp && (
            <span className="px-2 py-0.5 rounded bg-accent-950/50 text-accent-400 border border-accent-800 text-[11px] font-mono shrink-0 self-start sm:self-auto">
              Editing: {editingIp}
            </span>
          )}
        </div>

        <form onSubmit={handleSave} className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Source IP Address
            </label>
            <input
              type="text"
              value={ip}
              onChange={(e) => setIp(e.target.value)}
              disabled={Boolean(editingIp)}
              placeholder="e.g. 192.168.1.50"
              required
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono disabled:opacity-50"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Friendly Host Alias
            </label>
            <input
              type="text"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="e.g. Proxmox-Node-01"
              required
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Notes (Optional)
            </label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Main hypervisor in rack 1"
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500"
            />
          </div>

          <div className="sm:col-span-3 flex justify-end gap-2 pt-1">
            {editingIp && (
              <button
                type="button"
                onClick={handleCancelEdit}
                className="px-3.5 py-2 sm:py-1.5 bg-dark-800 hover:bg-dark-700 text-slate-300 rounded text-xs transition cursor-pointer"
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              disabled={isSaving || !ip.trim() || !alias.trim()}
              className="bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white font-medium px-4 py-2 sm:py-1.5 rounded text-xs flex items-center justify-center gap-1.5 transition shadow-xs cursor-pointer min-h-[38px] sm:min-h-0"
            >
              {editingIp ? <Check className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
              <span>{isSaving ? 'Saving...' : editingIp ? 'Update Mapping' : 'Add Mapping'}</span>
            </button>
          </div>
        </form>
      </section>

      {/* Aliases Table Card */}
      <section className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-md">
        <div className="p-3.5 sm:p-4 bg-dark-900 border-b border-dark-700 flex items-center justify-between">
          <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <Server className="w-4 h-4 text-accent-500" />
            <span>Active Host Mappings ({aliases.length})</span>
          </h3>
        </div>

        {isLoading ? (
          <div className="p-6 text-center text-slate-500 font-mono text-xs">Loading mappings...</div>
        ) : aliases.length === 0 ? (
          <div className="p-6 text-center text-slate-500 font-mono text-xs">
            No host aliases mapped yet. Add a mapping above to label incoming syslog IP addresses.
          </div>
        ) : isMobile ? (
          /* Mobile Card View */
          <div className="divide-y divide-dark-800">
            {aliases.map((item) => (
              <div key={item.ip} className="p-3.5 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-accent-400 font-semibold text-xs sm:text-sm">{item.ip}</span>
                  <div className="flex items-center gap-1 font-sans">
                    <button
                      onClick={() => handleEdit(item)}
                      title="Edit Alias"
                      className="p-2 text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      disabled={isDeletingIp === item.ip}
                      onClick={() => setDeleteTargetIp(item.ip)}
                      title="Delete Alias"
                      className="p-2 text-slate-400 hover:text-red-400 hover:bg-dark-800 rounded transition disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
                <div className="text-slate-200 font-mono text-xs font-medium">{item.alias}</div>
                {item.notes && (
                  <div className="text-slate-400 font-sans text-xs">
                    {item.notes}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          /* Desktop Table View */
          <div className="divide-y divide-dark-800 font-mono text-xs">
            <div className="grid grid-cols-[160px_200px_1fr_100px] px-4 py-2 text-slate-400 font-semibold text-[11px] bg-dark-950/60 select-none">
              <div>SOURCE IP</div>
              <div>FRIENDLY ALIAS</div>
              <div>NOTES</div>
              <div className="text-right">ACTIONS</div>
            </div>

            {aliases.map((item) => (
              <div
                key={item.ip}
                className="grid grid-cols-[160px_200px_1fr_100px] px-4 py-2.5 items-center hover:bg-dark-800/50 transition"
              >
                <div className="text-accent-400 font-semibold">{item.ip}</div>
                <div className="text-slate-200">{item.alias}</div>
                <div className="text-slate-400 font-sans text-xs truncate pr-2">
                  {item.notes || '-'}
                </div>
                <div className="flex items-center justify-end gap-1 font-sans">
                  <button
                    onClick={() => handleEdit(item)}
                    title="Edit Alias"
                    className="p-1 text-slate-400 hover:text-slate-200 hover:bg-dark-700 rounded transition cursor-pointer"
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    disabled={isDeletingIp === item.ip}
                    onClick={() => setDeleteTargetIp(item.ip)}
                    title="Delete Alias"
                    className="p-1 text-slate-400 hover:text-red-400 hover:bg-dark-700 rounded transition disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Delete Host Alias Confirmation Modal */}
      {deleteTargetIp && (
        <Modal
          isOpen={!!deleteTargetIp}
          onClose={() => setDeleteTargetIp(null)}
          title="Delete Host Alias"
          maxWidth="max-w-md"
        >
          <div className="space-y-4 text-xs font-sans">
            <div className="flex items-start gap-3 p-3 bg-red-950/30 border border-red-800/60 rounded-lg text-slate-200">
              <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
              <div className="space-y-2 flex-1">
                <p className="font-semibold text-slate-100 text-xs">
                  Remove host alias mapping?
                </p>
                <p className="text-slate-400 leading-relaxed">
                  Incoming logs from this IP address will no longer be assigned a friendly alias.
                </p>
                <div className="p-2.5 rounded bg-dark-950/80 border border-dark-700 font-mono text-[11px] text-slate-300 space-y-1">
                  <div>
                    <span className="text-slate-500">IP:</span> {deleteTargetIp}
                  </div>
                  {aliases.find((a) => a.ip === deleteTargetIp)?.alias && (
                    <div>
                      <span className="text-slate-500">Alias:</span>{' '}
                      {aliases.find((a) => a.ip === deleteTargetIp)?.alias}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setDeleteTargetIp(null)}
                className="px-3 py-1.5 text-xs bg-dark-800 hover:bg-dark-700 text-slate-300 border border-dark-600 rounded-lg transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isDeletingIp === deleteTargetIp}
                onClick={handleConfirmDelete}
                className="flex items-center gap-1.5 px-3.5 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg shadow transition cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>{isDeletingIp === deleteTargetIp ? 'Deleting...' : 'Delete Alias'}</span>
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
