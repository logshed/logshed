import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { SavedViewsMenu, SaveViewModal } from '../components/logs/SavedViewsMenu.tsx';
import { LogSearchBar } from '../components/logs/LogSearchBar.tsx';
import * as savedViewsApi from '../api/savedViews.ts';
import { SavedView } from '../types.ts';

describe('SavedViewsMenu Component', () => {
  const mockViews: SavedView[] = [
    {
      id: 1,
      name: 'Traefik Errors',
      query_params: { app: 'traefik', severity_max: 3 },
      is_pinned: true,
      created_at: '2026-09-18T10:00:00Z',
    },
    {
      id: 2,
      name: 'DHCP Traffic',
      query_params: { query: 'DHCP' },
      is_pinned: false,
      created_at: '2026-09-18T10:05:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Desktop Variant', () => {
    it('renders views dropdown button with count', () => {
      render(
        <SavedViewsMenu
          variant="desktop"
          views={mockViews}
          onApplyView={vi.fn()}
          onTogglePin={vi.fn()}
          onDeleteView={vi.fn()}
          onOpenSaveModal={vi.fn()}
        />
      );

      // Dropdown button should show count of views
      expect(screen.getByRole('button', { name: /Saved views menu/i })).toBeInTheDocument();
      expect(screen.getByText('Views')).toBeInTheDocument();
      expect(screen.getByText('2')).toBeInTheDocument();
    });

    it('opens dropdown menu and applies unpinned view on click', () => {
      const handleApply = vi.fn();
      render(
        <SavedViewsMenu
          variant="desktop"
          views={mockViews}
          onApplyView={handleApply}
          onTogglePin={vi.fn()}
          onDeleteView={vi.fn()}
          onOpenSaveModal={vi.fn()}
        />
      );

      // Open dropdown
      fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));
      expect(screen.getByTestId('views-dropdown-panel')).toBeInTheDocument();

      // Both views should be in the dropdown
      expect(screen.getByTestId('saved-view-item-1')).toBeInTheDocument();
      expect(screen.getByTestId('saved-view-item-2')).toBeInTheDocument();

      // Click DHCP Traffic item to apply
      fireEvent.click(screen.getByTestId('saved-view-item-2'));
      expect(handleApply).toHaveBeenCalledWith({ query: 'DHCP' });
    });

    it('closes dropdown when clicking the transparent backdrop without propagating to underlying elements', () => {
      render(
        <SavedViewsMenu
          variant="desktop"
          views={mockViews}
          onApplyView={vi.fn()}
          onTogglePin={vi.fn()}
          onDeleteView={vi.fn()}
          onOpenSaveModal={vi.fn()}
        />
      );

      fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));
      const backdrop = screen.getByTestId('views-dropdown-backdrop');
      expect(backdrop).toBeInTheDocument();

      fireEvent.click(backdrop);
      expect(screen.queryByTestId('views-dropdown-panel')).not.toBeInTheDocument();
    });

    it('calls onTogglePin and confirms onDeleteView from dropdown actions', () => {
      const handleTogglePin = vi.fn();
      const handleDelete = vi.fn();

      render(
        <SavedViewsMenu
          variant="desktop"
          views={mockViews}
          onApplyView={vi.fn()}
          onTogglePin={handleTogglePin}
          onDeleteView={handleDelete}
          onOpenSaveModal={vi.fn()}
        />
      );

      fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));

      const unpinBtn = screen.getByLabelText('Unpin view Traefik Errors');
      fireEvent.click(unpinBtn);
      expect(handleTogglePin).toHaveBeenCalled();

      const deleteBtn = screen.getByLabelText('Delete view DHCP Traffic');
      fireEvent.click(deleteBtn);

      // Confirmation modal should appear
      expect(screen.getByText('Delete "DHCP Traffic"?')).toBeInTheDocument();
      expect(screen.getByText('This saved filter view will be removed. This action cannot be undone.')).toBeInTheDocument();
      expect(handleDelete).not.toHaveBeenCalled();

      // Cancel deletion
      const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
      fireEvent.click(cancelBtn);
      expect(screen.queryByText('Delete "DHCP Traffic"?')).not.toBeInTheDocument();
      expect(handleDelete).not.toHaveBeenCalled();

      // Click delete again and confirm
      fireEvent.click(deleteBtn);
      expect(screen.getByText('Delete "DHCP Traffic"?')).toBeInTheDocument();

      const confirmDeleteBtn = screen.getByRole('button', { name: 'Delete View' });
      fireEvent.click(confirmDeleteBtn);
      expect(handleDelete).toHaveBeenCalledWith(expect.anything(), 2);
    });

    it('calls onOpenSaveModal when Save View button is clicked in dropdown', () => {
      const handleOpenModal = vi.fn();
      render(
        <SavedViewsMenu
          variant="desktop"
          views={mockViews}
          onApplyView={vi.fn()}
          onTogglePin={vi.fn()}
          onDeleteView={vi.fn()}
          onOpenSaveModal={handleOpenModal}
        />
      );

      fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));
      fireEvent.click(screen.getByRole('button', { name: /Save View/i }));

      expect(handleOpenModal).toHaveBeenCalledTimes(1);
    });
  });

  describe('Mobile Variant', () => {
    it('renders saved views in mobile section and triggers apply on tap', () => {
      const handleApply = vi.fn();
      render(
        <SavedViewsMenu
          variant="mobile"
          views={mockViews}
          onApplyView={handleApply}
          onTogglePin={vi.fn()}
          onDeleteView={vi.fn()}
          onOpenSaveModal={vi.fn()}
        />
      );

      expect(screen.getByText('Saved Views')).toBeInTheDocument();
      expect(screen.getByTestId('mobile-saved-view-1')).toBeInTheDocument();
      expect(screen.getByTestId('mobile-saved-view-2')).toBeInTheDocument();

      fireEvent.click(screen.getByTestId('mobile-saved-view-2'));
      expect(handleApply).toHaveBeenCalledWith({ query: 'DHCP' });
    });

    it('triggers onOpenSaveModal from mobile Save Current button', () => {
      const handleOpenModal = vi.fn();
      render(
        <SavedViewsMenu
          variant="mobile"
          views={mockViews}
          onApplyView={vi.fn()}
          onTogglePin={vi.fn()}
          onDeleteView={vi.fn()}
          onOpenSaveModal={handleOpenModal}
        />
      );

      fireEvent.click(screen.getByRole('button', { name: /Save Current/i }));
      expect(handleOpenModal).toHaveBeenCalledTimes(1);
    });

    it('confirms and deletes view from mobile drawer', () => {
      const handleDelete = vi.fn();
      render(
        <SavedViewsMenu
          variant="mobile"
          views={mockViews}
          onApplyView={vi.fn()}
          onTogglePin={vi.fn()}
          onDeleteView={handleDelete}
          onOpenSaveModal={vi.fn()}
        />
      );

      const deleteBtn = screen.getByLabelText('Delete view Traefik Errors');
      fireEvent.click(deleteBtn);

      expect(screen.getByText('Delete "Traefik Errors"?')).toBeInTheDocument();
      expect(handleDelete).not.toHaveBeenCalled();

      const confirmBtn = screen.getByRole('button', { name: 'Delete View' });
      fireEvent.click(confirmBtn);

      expect(handleDelete).toHaveBeenCalledWith(expect.anything(), 1);
    });
  });

  describe('SaveViewModal', () => {
    it('validates name input and submits new view details', async () => {
      const handleSave = vi.fn().mockResolvedValue(undefined);
      const handleClose = vi.fn();

      render(
        <SaveViewModal
          isOpen={true}
          onClose={handleClose}
          currentFilters={{ query: 'error', severity_max: 3 }}
          onSave={handleSave}
        />
      );

      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText('Active Filters to Save:')).toBeInTheDocument();

      const nameInput = screen.getByPlaceholderText('e.g. Traefik Errors, DHCP Traffic');
      fireEvent.change(nameInput, { target: { value: 'Production Errors' } });

      const submitBtn = screen.getByRole('button', { name: 'Save View' });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(handleSave).toHaveBeenCalledWith('Production Errors', true);
        expect(handleClose).toHaveBeenCalled();
      });
    });
  });

  describe('LogSearchBar Integration', () => {
    it('fetches views on mount and allows applying from search bar', async () => {
      vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue([...mockViews]);
      const handleApplyView = vi.fn();

      render(
        <LogSearchBar
          filters={{}}
          onFilterChange={vi.fn()}
          onSearch={vi.fn()}
          onReset={vi.fn()}
          onApplySavedView={handleApplyView}
        />
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Saved views menu/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));
      expect(screen.getByTestId('saved-view-item-1')).toBeInTheDocument();

      fireEvent.click(screen.getByTestId('saved-view-item-1'));
      expect(handleApplyView).toHaveBeenCalledWith({ app: 'traefik', severity_max: 3 });
    });
  });
});
