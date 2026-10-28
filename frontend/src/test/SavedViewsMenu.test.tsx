import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
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

describe('Rename saved views and the active view', () => {
  const views: SavedView[] = [
    { id: 1, name: 'Traefik Errors', query_params: { query: 'traefik', severity_max: 3 }, is_pinned: true, created_at: '2026-09-18T10:00:00Z' },
    { id: 2, name: 'DHCP Traffic', query_params: { query: 'DHCP' }, is_pinned: false, created_at: '2026-09-18T10:05:00Z' },
  ];

  const renderMenu = (props: Partial<React.ComponentProps<typeof SavedViewsMenu>> = {}) => {
    const handlers = {
      onApplyView: vi.fn(),
      onTogglePin: vi.fn(),
      onDeleteView: vi.fn(),
      onOpenSaveModal: vi.fn(),
      onRenameView: vi.fn().mockResolvedValue(undefined),
    };
    render(<SavedViewsMenu variant="desktop" views={views} {...handlers} {...props} />);
    return { ...handlers, ...props };
  };

  const openRenameFor = (name: string) => {
    fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));
    fireEvent.click(screen.getByLabelText(`Rename view ${name}`));
    return screen.getByLabelText(/View Name/) as HTMLInputElement;
  };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows Pin, Rename and Delete in that order in each row', () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));
    const row = screen.getByTestId('saved-view-item-2');
    const labels = Array.from(row.querySelectorAll('button')).map((b) => b.getAttribute('aria-label'));
    expect(labels).toEqual(['Pin view DHCP Traffic', 'Rename view DHCP Traffic', 'Delete view DHCP Traffic']);
    expect(screen.getByLabelText('Rename view DHCP Traffic')).toHaveAttribute('title', 'Rename view');
  });

  it('opens the rename modal without applying the view, closes the dropdown and selects the name', () => {
    const { onApplyView } = renderMenu();
    const input = openRenameFor('DHCP Traffic');
    expect(onApplyView).not.toHaveBeenCalled();
    expect(screen.queryByTestId('views-dropdown-panel')).toBeNull();
    expect(screen.getByText('Rename Saved View')).toBeInTheDocument();
    expect(input.value).toBe('DHCP Traffic');
    expect(input).toHaveAttribute('maxLength', '100');
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe('DHCP Traffic'.length);
  });

  it('disables Save for an empty or unchanged name, and allows a change of case', () => {
    renderMenu();
    const input = openRenameFor('DHCP Traffic');
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    fireEvent.change(input, { target: { value: '   ' } });
    expect(save).toBeDisabled();
    fireEvent.change(input, { target: { value: ' DHCP Traffic ' } });
    expect(save).toBeDisabled();
    fireEvent.change(input, { target: { value: 'dhcp traffic' } });
    expect(save).toBeEnabled();
    expect(screen.queryByText('A saved view with this name already exists.')).toBeNull();
  });

  it('warns about a name another view already uses, ignoring case', () => {
    renderMenu();
    const input = openRenameFor('DHCP Traffic');
    fireEvent.change(input, { target: { value: '  traefik errors ' } });
    expect(screen.getByText('A saved view with this name already exists.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('Enter saves the trimmed name and Esc closes without saving', async () => {
    const { onRenameView } = renderMenu();
    let input = openRenameFor('DHCP Traffic');
    fireEvent.change(input, { target: { value: 'Old name' } });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByText('Rename Saved View')).toBeNull();
    expect(onRenameView).not.toHaveBeenCalled();

    input = openRenameFor('DHCP Traffic');
    expect(input.value).toBe('DHCP Traffic');
    fireEvent.change(input, { target: { value: '  DHCP Leases  ' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    await waitFor(() => expect(onRenameView).toHaveBeenCalledWith(views[1], 'DHCP Leases'));
    await waitFor(() => expect(screen.queryByText('Rename Saved View')).toBeNull());
  });

  it('keeps the modal open with the error when the rename fails, and disables the buttons while saving', async () => {
    let reject: (err: Error) => void = () => {};
    const onRenameView = vi.fn(() => new Promise<void>((_resolve, rej) => { reject = rej; }));
    renderMenu({ onRenameView });
    const input = openRenameFor('DHCP Traffic');
    fireEvent.change(input, { target: { value: 'Router' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('button', { name: 'Saving...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    await act(async () => reject(new Error('A saved view named "Router" already exists.')));
    expect(await screen.findByText('A saved view named "Router" already exists.')).toBeInTheDocument();
    expect(screen.getByText('Rename Saved View')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
  });

  it('labels the Views button with the active view and highlights it in the dropdown', () => {
    renderMenu({ activeViewId: 1 });
    const button = screen.getByRole('button', { name: /Saved views menu/i });
    expect(button).toHaveTextContent('View: Traefik Errors');
    expect(button.className).toContain('border-accent-500');
    fireEvent.click(button);
    expect(screen.getByTestId('saved-view-item-1')).toHaveAttribute('aria-current', 'true');
    expect(screen.getByTestId('saved-view-item-2')).not.toHaveAttribute('aria-current');
  });

  it('mobile drawer highlights the active view and offers Rename next to Pin and Delete', async () => {
    const onApplyView = vi.fn();
    const onRenameView = vi.fn().mockResolvedValue(undefined);
    render(
      <SavedViewsMenu
        variant="mobile"
        views={views}
        activeViewId={2}
        onApplyView={onApplyView}
        onTogglePin={vi.fn()}
        onDeleteView={vi.fn()}
        onOpenSaveModal={vi.fn()}
        onRenameView={onRenameView}
      />
    );
    expect(screen.getByTestId('mobile-saved-view-2')).toHaveAttribute('aria-current', 'true');
    expect(screen.getByTestId('mobile-saved-view-1')).not.toHaveAttribute('aria-current');
    const labels = Array.from(screen.getByTestId('mobile-saved-view-1').querySelectorAll('button')).map((b) =>
      b.getAttribute('aria-label')
    );
    expect(labels).toEqual(['Unpin view Traefik Errors', 'Rename view Traefik Errors', 'Delete view Traefik Errors']);

    fireEvent.click(screen.getByLabelText('Rename view Traefik Errors'));
    expect(onApplyView).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/View Name/), { target: { value: 'Proxy Errors' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onRenameView).toHaveBeenCalledWith(views[0], 'Proxy Errors'));
  });

  it('a rename through the search bar calls updateSavedView with the trimmed name and updates the list and label', async () => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue(views.map((v) => ({ ...v })));
    const update = vi.spyOn(savedViewsApi, 'updateSavedView').mockResolvedValue({ ...views[0], name: 'Proxy Errors' });
    render(
      <LogSearchBar filters={{ severity_max: 3, query: 'traefik ' }} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} />
    );
    const button = await screen.findByRole('button', { name: /Saved views menu, active view Traefik Errors/i });
    expect(button).toHaveTextContent('View: Traefik Errors');

    fireEvent.click(button);
    fireEvent.click(screen.getByLabelText('Rename view Traefik Errors'));
    fireEvent.change(screen.getByLabelText(/View Name/), { target: { value: '  Proxy Errors  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { name: 'Proxy Errors' }));
    await waitFor(() => expect(screen.queryByText('Rename Saved View')).toBeNull());
    expect(screen.getByRole('button', { name: /Saved views menu/i })).toHaveTextContent('View: Proxy Errors');
    fireEvent.click(screen.getByRole('button', { name: /Saved views menu/i }));
    expect(screen.getByTestId('saved-view-item-1')).toHaveTextContent('Proxy Errors');
    expect(savedViewsApi.fetchSavedViews).toHaveBeenCalledTimes(1);
  });

  it('a server error from the rename API stays in the modal', async () => {
    vi.spyOn(savedViewsApi, 'fetchSavedViews').mockResolvedValue(views.map((v) => ({ ...v })));
    vi.spyOn(savedViewsApi, 'updateSavedView').mockRejectedValue(new Error('A saved view named "Router" already exists.'));
    render(<LogSearchBar filters={{}} onFilterChange={vi.fn()} onSearch={vi.fn()} onReset={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: /Saved views menu/i }));
    fireEvent.click(await screen.findByLabelText('Rename view DHCP Traffic'));
    fireEvent.change(screen.getByLabelText(/View Name/), { target: { value: 'Router' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('A saved view named "Router" already exists.')).toBeInTheDocument();
    expect(screen.getByText('Rename Saved View')).toBeInTheDocument();
    expect((screen.getByLabelText(/View Name/) as HTMLInputElement).value).toBe('Router');
  });
});

describe('SaveViewModal unique names', () => {
  const existing: SavedView[] = [
    { id: 1, name: 'Traefik Errors', query_params: {}, is_pinned: false, created_at: '2026-09-18T10:00:00Z' },
  ];

  it('warns while typing a name that is already taken, ignoring case', () => {
    const onSave = vi.fn();
    render(<SaveViewModal isOpen onClose={vi.fn()} currentFilters={{}} onSave={onSave} existingViews={existing} />);
    const input = screen.getByPlaceholderText('e.g. Traefik Errors, DHCP Traffic');
    fireEvent.change(input, { target: { value: ' traefik ERRORS ' } });
    expect(screen.getByText('A saved view with this name already exists.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save View' })).toBeDisabled();
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: 'Traefik Warnings' } });
    expect(screen.queryByText('A saved view with this name already exists.')).toBeNull();
    expect(screen.getByRole('button', { name: 'Save View' })).toBeEnabled();
  });

  it('shows a 409 from the server in the modal and keeps it open', async () => {
    const onClose = vi.fn();
    const onSave = vi.fn().mockRejectedValue(new Error('A saved view named "Fresh" already exists.'));
    render(<SaveViewModal isOpen onClose={onClose} currentFilters={{}} onSave={onSave} existingViews={existing} />);
    fireEvent.change(screen.getByPlaceholderText('e.g. Traefik Errors, DHCP Traffic'), { target: { value: 'Fresh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save View' }));
    expect(await screen.findByText('A saved view named "Fresh" already exists.')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
