import React, { useEffect, useState } from 'react';
import { Server, Plus, Trash2, Edit2, AlertCircle, ScrollText } from 'lucide-react';
import { HostAlias, SelfReportedHost } from '../../types.ts';
import { fetchAliases, fetchSelfReportedHosts, saveAlias, deleteAlias } from '../../api/aliases.ts';
import { HostAliasFormModal, HostAliasFormValues } from './HostAliasFormModal.tsx';
import { useMediaQuery, useNow, usePolling, useServerTimezone } from '../../utils/hooks.ts';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { useAlias } from '../../context/AliasContext.tsx';
import { Button, IconButton } from '../common/Button.tsx';
import { Card, CardHeader, SECTION_TITLE_CLASS } from '../common/Card.tsx';
import { SaveModeNote } from '../common/SaveModeNote.tsx';
import { Badge } from '../common/Badge.tsx';
import { TABLE_HEAD_ROW } from '../common/Table.tsx';
import { LastSeen } from '../common/LastSeen.tsx';

// How often the rows' last-seen times are fetched again while the page is open
const LAST_SEEN_REFRESH_MS = 30_000;

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

function lastSeenByIp(list: HostAlias[]): Record<string, string | null | undefined> {
  return Object.fromEntries(list.map((a) => [a.ip, a.last_seen]));
}

/** A self-reported row's actions: save its name as the alias at once, or open Add Host Alias with it. */
const SelfReportedRowActions: React.FC<{
  host: SelfReportedHost;
  isSaving: boolean;
  onMakeAlias: (host: SelfReportedHost) => void;
  onEdit: (host: SelfReportedHost) => void;
}> = ({ host, isSaving, onMakeAlias, onEdit }) => (
  <>
    <Button
      size="sm"
      variant="ghost"
      disabled={isSaving}
      onClick={() => onMakeAlias(host)}
      title={`Save ${host.names[0]} as the alias of ${host.ip}`}
    >
      {isSaving ? 'Saving...' : 'Make it the alias'}
    </Button>
    <IconButton size="sm" onClick={() => onEdit(host)} title="Add Host Alias" label={`Add an alias for ${host.ip}`}>
      <Edit2 className="w-3.5 h-3.5" />
    </IconButton>
  </>
);

/** The badge on a name a device reported for itself; its other names, if any, on hover. */
const SelfReportedBadge: React.FC<{ host: SelfReportedHost }> = ({ host }) => (
  <Badge
    title={
      host.names.length > 1
        ? `The name ${host.ip} sends itself (no alias set). It has also sent: ${host.names.slice(1).join(', ')}`
        : `The name ${host.ip} sends itself (no alias set)`
    }
  >
    Self-reported{host.names.length > 1 ? ` +${host.names.length - 1}` : ''}
  </Badge>
);

export const HostAliasManager: React.FC = () => {
  const { bumpAliasVersion } = useAlias();
  const [aliases, setAliases] = useState<HostAlias[]>([]);
  const [selfReported, setSelfReported] = useState<SelfReportedHost[]>([]);
  const [selfReportedCapped, setSelfReportedCapped] = useState<number | null>(null);
  const [makingAliasIp, setMakingAliasIp] = useState<string | null>(null);
  // Kept apart from aliases, so a refresh cannot reset an open Add or Edit dialog
  const [lastSeen, setLastSeen] = useState<Record<string, string | null | undefined>>({});
  const now = useNow(5000);
  const serverTimezone = useServerTimezone();
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // The Add / Edit dialog while it is open; a new key starts it afresh
  const [form, setForm] = useState<{ key: number; initial: HostAliasFormValues; editing: boolean } | null>(null);
  const openForm = (initial: HostAliasFormValues, editing: boolean) =>
    setForm((prev) => ({ key: (prev?.key ?? 0) + 1, initial, editing }));

  // Delete Modal state
  const [isDeletingIp, setIsDeletingIp] = useState<string | null>(null);
  const [deleteTargetIp, setDeleteTargetIp] = useState<string | null>(null);

  // The self-reported rows load on their own: the alias list does not wait for them, and a failure leaves them out
  const loadSelfReported = () => {
    fetchSelfReportedHosts()
      .then((res) => {
        setSelfReported(res.hosts);
        setSelfReportedCapped(res.truncated ? res.limit : null);
      })
      .catch(() => {});
  };

  const loadAliases = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);
      const list = await fetchAliases();
      setAliases(list);
      setLastSeen(lastSeenByIp(list));
      loadSelfReported();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load host aliases.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadAliases();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loads once on mount; loadAliases is a new function each render
  }, []);

  usePolling(() => {
    fetchAliases()
      .then((list) => setLastSeen(lastSeenByIp(list)))
      .catch(() => {});
    loadSelfReported();
  }, LAST_SEEN_REFRESH_MS);

  const openAddModal = () => openForm({ ip: '' }, false);

  const openEditModal = (item: HostAlias) => openForm({ ip: item.ip, alias: item.alias, notes: item.notes }, true);

  /** Add Host Alias with the address and the name it reported filled in. */
  const openAddFromReported = (host: SelfReportedHost) => openForm({ ip: host.ip, alias: host.names[0] ?? '' }, false);

  /** Saves the reported name as the address's alias at once, as the log drawer's Make it the alias does. */
  const handleMakeAlias = async (host: SelfReportedHost) => {
    if (makingAliasIp) return;
    try {
      setMakingAliasIp(host.ip);
      setErrorMsg(null);
      await saveAlias({ ip: host.ip, alias: host.names[0] });
      await loadAliases();
      bumpAliasVersion();
    } catch (err: any) {
      setErrorMsg(err.message || 'The alias could not be saved.');
    } finally {
      setMakingAliasIp(null);
    }
  };

  /** Opens the Console filtered to this alias (same URL hand-off the Navbar uses). */
  const handleViewLogs = (item: HostAlias) => {
    window.history.pushState(null, '', `/?source=${encodeURIComponent(item.alias)}`);
    window.dispatchEvent(new PopStateEvent('popstate'));
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
          description="Map incoming syslog source IP addresses to friendly host names (e.g. 192.168.1.1 to OPNsense Firewall). A host without an alias appears in the Console by the name it sends itself, listed below as self-reported, or by its IP address."
          note={<SaveModeNote mode="instant" />}
          actions={
            <Button variant="primary" onClick={openAddModal} icon={<Plus className="w-3.5 h-3.5" />}>
              Add Host Alias
            </Button>
          }
        />

        {/* Active Host Mappings Sub-Section */}
        <div className="p-3.5 sm:px-5 sm:py-3 bg-canvas/40 border-b border-line-subtle flex items-center justify-between">
          <h4 className={SECTION_TITLE_CLASS}>
            Active Host Mappings ({aliases.length}
            {selfReported.length > 0 ? `, ${selfReported.length} self-reported` : ''})
          </h4>
        </div>

        {isLoading ? (
          <div className="p-6 text-center text-fg-muted font-mono text-xs">Loading mappings...</div>
        ) : aliases.length === 0 && selfReported.length === 0 ? (
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
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-fg font-mono font-medium">{item.alias}</span>
                  <span className="text-fg-soft shrink-0 whitespace-nowrap">
                    <span className="text-fg-muted">Last seen </span>
                    <LastSeen at={lastSeen[item.ip]} now={now} timeZone={serverTimezone} />
                  </span>
                </div>
                {item.notes && (
                  <div className="text-fg-muted text-xs">
                    {item.notes}
                  </div>
                )}
              </div>
            ))}
            {selfReported.map((host) => (
              <div key={`reported-${host.ip}`} className="p-3.5 space-y-2" data-testid="self-reported-row">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-accent-400 font-semibold text-xs sm:text-sm truncate min-w-0" title={host.ip}>
                    {host.ip}
                  </span>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <SelfReportedRowActions
                      host={host}
                      isSaving={makingAliasIp === host.ip}
                      onMakeAlias={handleMakeAlias}
                      onEdit={openAddFromReported}
                    />
                  </div>
                </div>
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span className="text-fg-soft font-mono truncate" title={host.names[0]}>{host.names[0]}</span>
                    <SelfReportedBadge host={host} />
                  </span>
                  <span className="text-fg-soft shrink-0 whitespace-nowrap">
                    <span className="text-fg-muted">Last seen </span>
                    <LastSeen at={host.last_seen} now={now} timeZone={serverTimezone} />
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          /* Desktop Table View */
          <div className="divide-y divide-line-subtle text-xs">
            <div className={`grid grid-cols-[160px_200px_1fr_110px_160px] px-4 select-none ${TABLE_HEAD_ROW}`}>
              <div className="py-2.5">Source IP</div>
              <div className="py-2.5">Friendly Alias</div>
              <div className="py-2.5">Notes</div>
              <div className="py-2.5">Last seen</div>
              <div className="py-2.5 text-right">Actions</div>
            </div>

            {aliases.map((item) => (
              <div
                key={item.ip}
                className="grid grid-cols-[160px_200px_1fr_110px_160px] px-4 py-2.5 items-center hover:bg-raised/50 transition"
              >
                <div className="font-mono text-accent-400 font-semibold">{item.ip}</div>
                <div className="font-mono text-fg">{item.alias}</div>
                <div className="text-fg-muted truncate pr-2">
                  {item.notes || '-'}
                </div>
                <div className="text-fg-soft">
                  <LastSeen at={lastSeen[item.ip]} now={now} timeZone={serverTimezone} />
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
            {selfReported.map((host) => (
              <div
                key={`reported-${host.ip}`}
                data-testid="self-reported-row"
                className="grid grid-cols-[160px_200px_1fr_110px_160px] px-4 py-2.5 items-center hover:bg-raised/50 transition"
              >
                <div className="font-mono text-accent-400 font-semibold">{host.ip}</div>
                <div className="flex items-center gap-1.5 min-w-0 pr-2">
                  <span className="font-mono text-fg-soft truncate" title={host.names[0]}>{host.names[0]}</span>
                  <SelfReportedBadge host={host} />
                </div>
                <div className="text-fg-muted truncate pr-2">No alias set</div>
                <div className="text-fg-soft">
                  <LastSeen at={host.last_seen} now={now} timeZone={serverTimezone} />
                </div>
                <div className="flex items-center justify-end gap-1.5">
                  <SelfReportedRowActions
                    host={host}
                    isSaving={makingAliasIp === host.ip}
                    onMakeAlias={handleMakeAlias}
                    onEdit={openAddFromReported}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
        {selfReportedCapped !== null && (
          <div className="px-4 py-2.5 border-t border-line-subtle text-xs text-fg-muted">
            Showing the {selfReportedCapped} self-reported hosts seen most recently.
          </div>
        )}
      </Card>

      {/* Add / Edit Host Alias Modal */}
      {form && (
        <HostAliasFormModal
          key={form.key}
          initial={form.initial}
          editing={form.editing}
          onClose={() => setForm(null)}
          onSaved={async () => {
            await loadAliases();
            bumpAliasVersion();
          }}
        />
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
