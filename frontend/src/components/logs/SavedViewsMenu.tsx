import React, { useState, useRef, useEffect } from 'react';
import { Bookmark, Pin, Plus, Trash2, ChevronDown, AlertTriangle } from 'lucide-react';
import { LogFilterParams, SavedView } from '../../types.ts';
import { Modal } from '../common/Modal.tsx';

interface SavedViewsMenuProps {
  variant: 'desktop' | 'mobile';
  views: SavedView[];
  isLoading?: boolean;
  onApplyView: (params: LogFilterParams) => void;
  onTogglePin: (e: React.MouseEvent, view: SavedView) => void;
  onDeleteView: (e: React.MouseEvent, viewId: number) => void;
  onOpenSaveModal: () => void;
}

export const SavedViewsMenu: React.FC<SavedViewsMenuProps> = ({
  variant,
  views,
  onApplyView,
  onTogglePin,
  onDeleteView,
  onOpenSaveModal,
}) => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [viewToDelete, setViewToDelete] = useState<SavedView | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

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

  // Handle escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        setIsOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen]);

  // Define deletion confirmation modal
  const deleteConfirmModal = viewToDelete && (
    <Modal
      isOpen={!!viewToDelete}
      onClose={() => setViewToDelete(null)}
      title="Delete Saved View"
      maxWidth="max-w-md"
    >
      <div className="space-y-4 text-xs font-sans">
        <div className="flex items-start gap-3 p-3 bg-red-950/30 border border-red-800/60 rounded-lg text-slate-200">
          <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-semibold text-slate-100 text-xs">
              Delete &quot;{viewToDelete.name}&quot;?
            </p>
            <p className="text-slate-400 leading-relaxed">
              This saved filter view will be removed. This action cannot be undone.
            </p>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={() => setViewToDelete(null)}
            className="px-3 py-1.5 text-xs bg-dark-800 hover:bg-dark-700 text-slate-300 border border-dark-600 rounded-lg transition cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={(e) => {
              onDeleteView(e, viewToDelete.id);
              setViewToDelete(null);
            }}
            className="flex items-center gap-1.5 px-3.5 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg shadow transition cursor-pointer"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Delete View</span>
          </button>
        </div>
      </div>
    </Modal>
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
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition cursor-pointer border ${
              isOpen
                ? 'bg-dark-800 text-white border-dark-600'
                : 'bg-dark-900 text-slate-300 border-dark-700 hover:text-white hover:border-slate-600'
            }`}
            title="Saved views menu"
            aria-label="Saved views menu"
            aria-expanded={isOpen}
          >
            <Bookmark className="w-3 h-3 text-accent-400" />
            <span>Views</span>
            {views.length > 0 && (
              <span className="bg-dark-800 text-slate-400 text-[10px] font-mono px-1 rounded-full">
                {views.length}
              </span>
            )}
            <ChevronDown
              className={`w-3 h-3 text-slate-400 transition-transform duration-150 ${
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
                className="absolute right-0 top-full mt-1.5 w-64 bg-dark-900 border border-dark-700 rounded-md shadow-xl py-1 z-50 font-sans"
              >
                {/* Header Actions */}
                <div className="flex items-center justify-between px-3 py-1.5 border-b border-dark-800 text-[11px] font-medium text-slate-400">
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
                    <div className="px-3 py-4 text-center text-slate-400 text-xs">
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
                        className={`group flex items-center justify-between px-3 py-1.5 text-xs hover:bg-dark-800 cursor-pointer transition ${
                          view.is_pinned ? 'text-accent-300 font-medium' : 'text-slate-300'
                        }`}
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
                            className="p-1 hover:text-accent-300 text-slate-400 rounded transition cursor-pointer"
                            title={view.is_pinned ? 'Unpin view' : 'Pin view'}
                            aria-label={view.is_pinned ? `Unpin view ${view.name}` : `Pin view ${view.name}`}
                          >
                            <Pin className={`w-2.5 h-2.5 ${view.is_pinned ? 'text-accent-400' : ''}`} />
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setViewToDelete(view);
                            }}
                            className="p-1 hover:text-red-400 text-slate-400 rounded transition cursor-pointer"
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
      </div>
    );
  }

  // Mobile Drawer Section Variant
  return (
    <div className="pb-3 border-b border-dark-800">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 text-slate-300 font-medium">
          <Bookmark className="w-3.5 h-3.5 text-accent-400" />
          <span>Saved Views</span>
          {views.length > 0 && (
            <span className="bg-dark-900 text-slate-400 border border-dark-700 text-[10px] font-mono px-1.5 rounded-full">
              {views.length}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onOpenSaveModal}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-dark-900 hover:bg-dark-800 text-accent-400 border border-dark-700 transition cursor-pointer"
        >
          <Plus className="w-3 h-3" />
          <span>Save Current</span>
        </button>
      </div>

      {views.length === 0 ? (
        <div className="text-[11px] text-slate-400 italic">
          No saved views yet. Save current filters to create one.
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto pr-1">
          {views.map((view) => (
            <div
              key={view.id}
              data-testid={`mobile-saved-view-${view.id}`}
              onClick={() => onApplyView(view.query_params)}
              className={`group flex items-center gap-1 px-2 py-1 rounded text-xs transition cursor-pointer border ${
                view.is_pinned
                  ? 'bg-accent-950/40 text-accent-300 border-accent-700/60 font-medium'
                  : 'bg-dark-900 text-slate-300 border-dark-700 hover:bg-dark-800'
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
                  className="p-0.5 hover:text-accent-300 text-slate-400"
                  aria-label={view.is_pinned ? `Unpin view ${view.name}` : `Pin view ${view.name}`}
                >
                  <Pin className={`w-2.5 h-2.5 ${view.is_pinned ? 'text-accent-400' : ''}`} />
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setViewToDelete(view);
                  }}
                  className="p-0.5 hover:text-red-400 text-slate-400 cursor-pointer"
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
    </div>
  );
};

interface SaveViewModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentFilters: LogFilterParams;
  onSave: (name: string, isPinned: boolean) => Promise<void>;
}

export const SaveViewModal: React.FC<SaveViewModalProps> = ({
  isOpen,
  onClose,
  currentFilters,
  onSave,
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
          <label className="block text-slate-300 font-medium mb-1">
            View Name <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Traefik Errors, DHCP Traffic"
            maxLength={100}
            autoFocus
            className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-1.5 text-slate-200 placeholder-slate-400 focus:outline-hidden focus:border-accent-500"
          />
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="view-pinned-checkbox"
            checked={isPinned}
            onChange={(e) => setIsPinned(e.target.checked)}
            className="rounded bg-dark-950 border-dark-700 text-accent-500 focus:ring-accent-500/20"
          />
          <label htmlFor="view-pinned-checkbox" className="text-slate-300 cursor-pointer">
            Pin view for quick access
          </label>
        </div>

        {/* Active Filter Summary */}
        <div className="bg-dark-950/60 border border-dark-800 rounded p-2 text-slate-400 space-y-1">
          <div className="font-semibold text-slate-300 text-[11px] mb-1">Active Filters to Save:</div>
          {hasActiveFilters ? (
            <ul className="list-disc list-inside space-y-0.5 text-[11px]">
              {currentFilters.query && (
                <li>Search text: <span className="text-slate-200 font-mono">{currentFilters.query}</span></li>
              )}
              {currentFilters.severity_max !== undefined && (
                <li>Max severity: <span className="text-slate-200 font-mono">{currentFilters.severity_max}</span></li>
              )}
              {((currentFilters.sources && currentFilters.sources.length > 0) || currentFilters.source) && (
                <li>Sources: <span className="text-slate-200 font-mono">{(currentFilters.sources || [currentFilters.source]).join(', ')}</span></li>
              )}
              {((currentFilters.apps && currentFilters.apps.length > 0) || currentFilters.app_name) && (
                <li>Apps: <span className="text-slate-200 font-mono">{(currentFilters.apps || [currentFilters.app_name]).join(', ')}</span></li>
              )}
              {currentFilters.from && <li>From: <span className="text-slate-200 font-mono">{currentFilters.from}</span></li>}
              {currentFilters.to && <li>To: <span className="text-slate-200 font-mono">{currentFilters.to}</span></li>}
            </ul>
          ) : (
            <div className="italic text-[11px]">No active filters (will display all incoming logs).</div>
          )}
        </div>

        {error && (
          <div className="text-red-400 bg-red-950/30 border border-red-800/40 rounded p-2 text-[11px]">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-dark-800">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 rounded text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSaving || !name.trim()}
            className="px-3 py-1.5 rounded bg-accent-600 hover:bg-accent-500 text-white font-medium transition disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : 'Save View'}
          </button>
        </div>
      </form>
    </Modal>
  );
};
