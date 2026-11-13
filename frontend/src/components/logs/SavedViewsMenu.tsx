import React, { useState, useRef, useEffect } from 'react';
import { Bookmark, Pin, Plus, Trash2, ChevronDown, Pencil } from 'lucide-react';
import { LogFilterParams, SavedView } from '../../types.ts';
import { Modal } from '../common/Modal.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { isSavedViewNameTaken } from './useSavedViews.ts';
import { NAME_MAX_LENGTH } from '../../utils/fieldLimits.ts';

export const DUPLICATE_VIEW_NAME_MESSAGE = 'A saved view with this name already exists.';

interface SavedViewsMenuProps {
  variant: 'desktop' | 'mobile';
  views: SavedView[];
  isLoading?: boolean;
  /** The saved view whose filters equal the current filters, if any. */
  activeViewId?: number | null;
  onApplyView: (params: LogFilterParams) => void;
  onTogglePin: (e: React.MouseEvent, view: SavedView) => void;
  /** Renames a view; a thrown error is shown in the rename modal. Without it there is no rename action. */
  onRenameView?: (view: SavedView, name: string) => Promise<void>;
  onDeleteView: (e: React.MouseEvent, viewId: number) => void;
  onOpenSaveModal: () => void;
}

export const SavedViewsMenu: React.FC<SavedViewsMenuProps> = ({
  variant,
  views,
  activeViewId = null,
  onApplyView,
  onTogglePin,
  onRenameView,
  onDeleteView,
  onOpenSaveModal,
}) => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [viewToDelete, setViewToDelete] = useState<SavedView | null>(null);
  const [viewToRename, setViewToRename] = useState<SavedView | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const activeView = activeViewId !== null ? views.find((v) => v.id === activeViewId) ?? null : null;

  const renameModal = onRenameView && (
    <RenameViewModal
      view={viewToRename}
      views={views}
      onClose={() => setViewToRename(null)}
      onRename={onRenameView}
    />
  );

  const openRename = (e: React.MouseEvent, view: SavedView) => {
    e.stopPropagation();
    setIsOpen(false);
    setViewToRename(view);
  };

  // Close dropdown on outside click
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  // Esc closes the menu, and only the menu: captured and stopped before a dialog around the menu
  // (the mobile filter drawer) sees it. While the menu's own delete or rename dialog is open,
  // that dialog is on top and takes Esc instead.
  const ownDialogOpen = viewToDelete !== null || viewToRename !== null;
  useEffect(() => {
    if (!isOpen || ownDialogOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setIsOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [isOpen, ownDialogOpen]);

  // Define deletion confirmation modal
  const deleteConfirmModal = viewToDelete && (
    <ConfirmDialog
      isOpen={!!viewToDelete}
      title="Delete Saved View"
      look="notice"
      noticeClassName="space-y-1"
      onCancel={() => setViewToDelete(null)}
      onConfirm={(e) => {
        onDeleteView(e, viewToDelete.id);
        setViewToDelete(null);
      }}
      confirmIcon={<Trash2 className="w-3.5 h-3.5" />}
      confirmLabel="Delete View"
    >
      <p className="font-semibold text-fg-strong text-xs">
        Delete &quot;{viewToDelete.name}&quot;?
      </p>
      <p className="text-fg-muted leading-relaxed">
        This saved filter view will be removed. This action cannot be undone.
      </p>
    </ConfirmDialog>
  );

  // Desktop Dropdown Variant
  if (variant === 'desktop') {
    return (
      <div className="ml-auto flex items-center" ref={dropdownRef}>
        {/* Dropdown Trigger Button */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setIsOpen((prev) => !prev)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition cursor-pointer border max-w-[16rem] ${
              activeView
                ? `border-accent-500 ${isOpen ? 'bg-raised text-fg-max' : 'bg-surface text-accent-200 hover:text-fg-max'}`
                : isOpen
                  ? 'bg-raised text-fg-max border-line-strong'
                  : 'bg-surface text-fg-soft border-line hover:text-fg-max hover:border-line-stronger'
            }`}
            title={activeView ? `Saved views menu (active view: ${activeView.name})` : 'Saved views menu'}
            aria-label={activeView ? `Saved views menu, active view ${activeView.name}` : 'Saved views menu'}
            aria-expanded={isOpen}
          >
            <Bookmark className={`w-3 h-3 text-accent-400 shrink-0 ${activeView ? 'fill-accent-400' : ''}`} />
            {activeView ? (
              <span data-testid="views-button-label" className="truncate">View: {activeView.name}</span>
            ) : (
              <span data-testid="views-button-label">Views</span>
            )}
            {!activeView && views.length > 0 && (
              <span className="bg-raised text-fg-muted text-[10px] font-mono px-1 rounded-full">
                {views.length}
              </span>
            )}
            <ChevronDown
              className={`w-3 h-3 text-fg-muted transition-transform duration-150 ${
                isOpen ? 'rotate-180' : ''
              }`}
            />
          </button>

          {/* Floating Dropdown Panel & Transparent Click-Away Backdrop */}
          {isOpen && (
            <>
              {/* Fixed transparent backdrop that captures click-aways to prevent triggering underlying elements like log rows */}
              <div
                data-testid="views-dropdown-backdrop"
                className="fixed inset-0 z-40 bg-transparent"
                onClick={(e) => {
                  e.stopPropagation();
                  setIsOpen(false);
                }}
              />

              <div
                data-testid="views-dropdown-panel"
                data-no-pull-refresh
                className="absolute right-0 top-full mt-1.5 w-64 bg-surface border border-line rounded-md shadow-xl py-1 z-50 font-sans"
              >
                {/* Header Actions */}
                <div className="flex items-center justify-between px-3 py-1.5 border-b border-line-subtle text-[11px] font-medium text-fg-muted">
                  <span className="flex items-center gap-1.5">
                    <Bookmark className="w-3 h-3 text-accent-400" />
                    <span>Saved Views</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setIsOpen(false);
                      onOpenSaveModal();
                    }}
                    className="flex items-center gap-1 text-accent-400 hover:text-accent-300 transition cursor-pointer"
                  >
                    <Plus className="w-3 h-3" />
                    <span>Save View</span>
                  </button>
                </div>

                {/* View List */}
                <div className="max-h-60 overflow-y-auto py-1">
                  {views.length === 0 ? (
                    <div className="px-3 py-4 text-center text-fg-muted text-xs">
                      No saved views yet.
                    </div>
                  ) : (
                    views.map((view) => (
                      <div
                        key={view.id}
                        data-testid={`saved-view-item-${view.id}`}
                        onClick={() => {
                          onApplyView(view.query_params);
                          setIsOpen(false);
                        }}
                        aria-current={view.id === activeViewId ? 'true' : undefined}
                        className={`group flex items-center justify-between px-3 py-1.5 text-xs hover:bg-raised cursor-pointer transition ${
                          view.is_pinned ? 'text-accent-300 font-medium' : 'text-fg-soft'
                        } ${view.id === activeViewId ? 'bg-raised' : ''}`}
                      >
                        <div className="flex items-center gap-2 truncate flex-1 min-w-0">
                          {view.is_pinned && (
                            <Pin className="w-2.5 h-2.5 text-accent-400 fill-accent-400 shrink-0" />
                          )}
                          <span className="truncate">{view.name}</span>
                        </div>
                        <div className="flex items-center ml-2 gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onTogglePin(e, view);
                            }}
                            className="p-1 hover:text-accent-300 text-fg-muted rounded-sm transition cursor-pointer"
                            title={view.is_pinned ? 'Unpin view' : 'Pin view'}
                            aria-label={view.is_pinned ? `Unpin view ${view.name}` : `Pin view ${view.name}`}
                          >
                            <Pin className={`w-2.5 h-2.5 ${view.is_pinned ? 'text-accent-400' : ''}`} />
                          </button>
                          {onRenameView && (
                            <button
                              type="button"
                              onClick={(e) => openRename(e, view)}
                              className="p-1 hover:text-accent-300 text-fg-muted rounded-sm transition cursor-pointer"
                              title="Rename view"
                              aria-label={`Rename view ${view.name}`}
                            >
                              <Pencil className="w-2.5 h-2.5" />
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setViewToDelete(view);
                            }}
                            className="p-1 hover:text-danger-400 text-fg-muted rounded-sm transition cursor-pointer"
                            title="Delete view"
                            aria-label={`Delete view ${view.name}`}
                          >
                            <Trash2 className="w-2.5 h-2.5" />
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </div>
        {deleteConfirmModal}
        {renameModal}
      </div>
    );
  }

  // Mobile Drawer Section Variant
  return (
    <div className="pb-3 border-b border-line-subtle">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 text-fg-soft font-medium">
          <Bookmark className="w-3.5 h-3.5 text-accent-400" />
          <span>Saved Views</span>
          {views.length > 0 && (
            <span className="bg-surface text-fg-muted border border-line text-[10px] font-mono px-1.5 rounded-full">
              {views.length}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onOpenSaveModal}
          className="flex items-center gap-1 px-2 py-0.5 rounded-sm text-[11px] font-medium bg-surface hover:bg-raised text-accent-400 border border-line transition cursor-pointer"
        >
          <Plus className="w-3 h-3" />
          <span>Save Current</span>
        </button>
      </div>

      {views.length === 0 ? (
        <div className="text-[11px] text-fg-muted italic">
          No saved views yet. Save current filters to create one.
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto pr-1">
          {views.map((view) => (
            <div
              key={view.id}
              data-testid={`mobile-saved-view-${view.id}`}
              onClick={() => onApplyView(view.query_params)}
              aria-current={view.id === activeViewId ? 'true' : undefined}
              className={`group flex items-center gap-1 px-2 py-1 rounded text-xs transition cursor-pointer border ${
                view.id === activeViewId
                  ? 'bg-accent-900/60 text-on-fill border-accent-500 ring-1 ring-accent-500 font-semibold'
                  : view.is_pinned
                    ? 'bg-accent-950/40 text-accent-300 border-accent-700/60 font-medium'
                    : 'bg-surface text-fg-soft border-line hover:bg-raised'
              }`}
            >
              {view.is_pinned && <Pin className="w-3 h-3 text-accent-400 fill-accent-400 shrink-0" />}
              <span className="truncate max-w-[130px]">{view.name}</span>
              <div className="flex items-center ml-1 gap-0.5 opacity-60 group-hover:opacity-100">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onTogglePin(e, view);
                  }}
                  className="p-0.5 hover:text-accent-300 text-fg-muted"
                  aria-label={view.is_pinned ? `Unpin view ${view.name}` : `Pin view ${view.name}`}
                >
                  <Pin className={`w-2.5 h-2.5 ${view.is_pinned ? 'text-accent-400' : ''}`} />
                </button>
                {onRenameView && (
                  <button
                    type="button"
                    onClick={(e) => openRename(e, view)}
                    className="p-0.5 hover:text-accent-300 text-fg-muted cursor-pointer"
                    title="Rename view"
                    aria-label={`Rename view ${view.name}`}
                  >
                    <Pencil className="w-2.5 h-2.5" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setViewToDelete(view);
                  }}
                  className="p-0.5 hover:text-danger-400 text-fg-muted cursor-pointer"
                  aria-label={`Delete view ${view.name}`}
                >
                  <Trash2 className="w-2.5 h-2.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {deleteConfirmModal}
      {renameModal}
    </div>
  );
};

interface RenameViewModalProps {
  /** The view being renamed; the modal is open while this is set. */
  view: SavedView | null;
  /** All loaded views, to check the new name is not taken. */
  views: SavedView[];
  onClose: () => void;
  onRename: (view: SavedView, name: string) => Promise<void>;
}

export const RenameViewModal: React.FC<RenameViewModalProps> = ({ view, ...rest }) =>
  // Keyed by view so each opening starts from that view's name
  view ? <RenameViewDialog key={view.id} view={view} {...rest} /> : null;

const RenameViewDialog: React.FC<RenameViewModalProps & { view: SavedView }> = ({ view, views, onClose, onRename }) => {
  const [name, setName] = useState<string>(view.name);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const trimmed = name.trim();
  const isTaken = isSavedViewNameTaken(views, trimmed, view.id);
  // A different case of the same name counts as a change
  const isUnchanged = trimmed === view.name;
  const canSave = !isSaving && trimmed.length > 0 && !isUnchanged && !isTaken;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSave) return;
    try {
      setIsSaving(true);
      setError(null);
      await onRename(view, trimmed);
      onClose();
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : 'Could not rename the view.');
      setIsSaving(false);
    }
  };

  return (
    <Modal isOpen={true} onClose={onClose} title="Rename Saved View" maxWidth="max-w-md">
      <form onSubmit={handleSubmit} className="space-y-4 text-xs">
        <div>
          <label htmlFor="rename-view-input" className="block text-fg-soft font-medium mb-1">
            View Name <span className="text-danger-400">*</span>
          </label>
          <input
            id="rename-view-input"
            ref={inputRef}
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={NAME_MAX_LENGTH}
            aria-invalid={isTaken || undefined}
            aria-describedby={isTaken ? 'rename-view-taken' : undefined}
            className="w-full bg-canvas border border-line rounded-sm px-3 py-1.5 text-fg placeholder-fg-muted focus:outline-hidden focus:border-accent-500"
          />
          {isTaken && (
            <p id="rename-view-taken" className="mt-1 text-danger-400 text-[11px]">
              {DUPLICATE_VIEW_NAME_MESSAGE}
            </p>
          )}
        </div>

        {error && (
          <div role="alert" className="text-danger-400 bg-danger-950/30 border border-danger-800/40 rounded-sm p-2 text-[11px]">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-line-subtle">
          <button
            type="button"
            onClick={onClose}
            disabled={isSaving}
            className="px-3 py-1.5 rounded-sm text-fg-muted hover:text-fg hover:bg-raised transition disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSave}
            className="px-3 py-1.5 rounded-sm bg-accent-700 hover:bg-accent-600 text-on-fill font-medium transition disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  );
};

interface SaveViewModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentFilters: LogFilterParams;
  onSave: (name: string, isPinned: boolean) => Promise<void>;
  /** Loaded views, to warn before saving a name that is already taken (ignoring case). */
  existingViews?: SavedView[];
}

export const SaveViewModal: React.FC<SaveViewModalProps> = ({
  isOpen,
  onClose,
  currentFilters,
  onSave,
  existingViews = [],
}) => {
  const [name, setName] = useState<string>('');
  const [isPinned, setIsPinned] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setName('');
      setIsPinned(true);
      setError(null);
    }
  }, [isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError('View name is required.');
      return;
    }
    if (isSavedViewNameTaken(existingViews, trimmed)) return;

    try {
      setIsSaving(true);
      setError(null);
      await onSave(trimmed, isPinned);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to save view.');
    } finally {
      setIsSaving(false);
    }
  };

  const isTaken = isSavedViewNameTaken(existingViews, name);

  const hasActiveFilters = Boolean(
    currentFilters.query?.trim() ||
    currentFilters.severity_max !== undefined ||
    (currentFilters.sources && currentFilters.sources.length > 0) ||
    currentFilters.source ||
    (currentFilters.apps && currentFilters.apps.length > 0) ||
    currentFilters.app_name ||
    currentFilters.from ||
    currentFilters.to
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Save Current View"
      maxWidth="max-w-md"
    >
      <form onSubmit={handleSubmit} className="space-y-4 text-xs">
        <div>
          <label className="block text-fg-soft font-medium mb-1">
            View Name <span className="text-danger-400">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Traefik Errors, DHCP Traffic"
            maxLength={NAME_MAX_LENGTH}
            autoFocus
            aria-invalid={isTaken || undefined}
            aria-describedby={isTaken ? 'save-view-taken' : undefined}
            className="w-full bg-canvas border border-line rounded-sm px-3 py-1.5 text-fg placeholder-fg-muted focus:outline-hidden focus:border-accent-500"
          />
          {isTaken && (
            <p id="save-view-taken" className="mt-1 text-danger-400 text-[11px]">
              {DUPLICATE_VIEW_NAME_MESSAGE}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="view-pinned-checkbox"
            checked={isPinned}
            onChange={(e) => setIsPinned(e.target.checked)}
            className="rounded-sm bg-canvas border-line text-accent-500 focus:ring-accent-500/20"
          />
          <label htmlFor="view-pinned-checkbox" className="text-fg-soft cursor-pointer">
            Pin view for quick access
          </label>
        </div>

        {/* Active Filter Summary */}
        <div className="bg-canvas/60 border border-line-subtle rounded-sm p-2 text-fg-muted space-y-1">
          <div className="font-semibold text-fg-soft text-[11px] mb-1">Active Filters to Save:</div>
          {hasActiveFilters ? (
            <ul className="list-disc list-inside space-y-0.5 text-[11px]">
              {currentFilters.query && (
                <li>Search text: <span className="text-fg font-mono">{currentFilters.query}</span></li>
              )}
              {currentFilters.severity_max !== undefined && (
                <li>Max severity: <span className="text-fg font-mono">{currentFilters.severity_max}</span></li>
              )}
              {((currentFilters.sources && currentFilters.sources.length > 0) || currentFilters.source) && (
                <li>Sources: <span className="text-fg font-mono">{(currentFilters.sources || [currentFilters.source]).join(', ')}</span></li>
              )}
              {((currentFilters.apps && currentFilters.apps.length > 0) || currentFilters.app_name) && (
                <li>Apps: <span className="text-fg font-mono">{(currentFilters.apps || [currentFilters.app_name]).join(', ')}</span></li>
              )}
              {currentFilters.from && <li>From: <span className="text-fg font-mono">{currentFilters.from}</span></li>}
              {currentFilters.to && <li>To: <span className="text-fg font-mono">{currentFilters.to}</span></li>}
            </ul>
          ) : (
            <div className="italic text-[11px]">No active filters (will display all incoming logs).</div>
          )}
        </div>

        {error && (
          <div className="text-danger-400 bg-danger-950/30 border border-danger-800/40 rounded-sm p-2 text-[11px]">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-line-subtle">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded-sm text-fg-muted hover:text-fg hover:bg-raised transition"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSaving || !name.trim() || isTaken}
            className="px-3 py-1.5 rounded-sm bg-accent-700 hover:bg-accent-600 text-on-fill font-medium transition disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : 'Save View'}
          </button>
        </div>
      </form>
    </Modal>
  );
};
