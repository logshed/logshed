import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { HostAliasManager } from '../components/aliases/HostAliasManager.tsx';
import * as aliasesApi from '../api/aliases.ts';

describe('HostAliasManager Component', () => {
  const mockAliases = [
    {
      ip: '192.168.1.1',
      alias: 'router.local',
      notes: 'Main router',
      created_at: '2026-09-01T10:00:00Z',
    },
    {
      ip: '192.168.1.50',
      alias: 'proxmox-01',
      notes: null,
      created_at: '2026-09-02T11:00:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(aliasesApi, 'fetchAliases').mockResolvedValue(mockAliases);
  });

  it('renders active host aliases in the table', async () => {
    render(<HostAliasManager />);

    await waitFor(() => {
      expect(screen.getByText('Active Host Mappings (2)')).toBeInTheDocument();
    });

    expect(screen.getByText('192.168.1.1')).toBeInTheDocument();
    expect(screen.getByText('router.local')).toBeInTheDocument();
    expect(screen.getByText('Main router')).toBeInTheDocument();
    expect(screen.getByText('192.168.1.50')).toBeInTheDocument();
    expect(screen.getByText('proxmox-01')).toBeInTheDocument();
  });

  it('opens confirmation modal and disables confirm button while deletion is pending', async () => {
    let resolveDelete: (val: any) => void;
    const deletePromise = new Promise((resolve) => {
      resolveDelete = resolve;
    });
    const deleteSpy = vi.spyOn(aliasesApi, 'deleteAlias').mockImplementation(() => deletePromise as any);

    render(<HostAliasManager />);

    await waitFor(() => {
      expect(screen.getByText('192.168.1.1')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByTitle('Delete Alias');
    const firstDeleteBtn = deleteButtons[0];

    // Click trash button to trigger confirmation modal
    fireEvent.click(firstDeleteBtn);

    // Modal dialog should now be open
    let dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Delete Host Alias')).toBeInTheDocument();
    expect(within(dialog).getByText('Remove host alias mapping?')).toBeInTheDocument();

    // Verify Cancel button closes modal without deleting
    const cancelBtn = within(dialog).getByRole('button', { name: 'Cancel' });
    fireEvent.click(cancelBtn);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(deleteSpy).not.toHaveBeenCalled();

    // Reopen modal and confirm deletion
    fireEvent.click(firstDeleteBtn);
    dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Delete Host Alias')).toBeInTheDocument();

    const confirmDeleteBtn = within(dialog).getByRole('button', { name: /Delete Alias/i });
    expect(confirmDeleteBtn).not.toBeDisabled();

    // Click confirm delete in modal
    fireEvent.click(confirmDeleteBtn);

    // Button should now be disabled and show deleting state
    expect(confirmDeleteBtn).toBeDisabled();
    expect(within(dialog).getByText('Deleting...')).toBeInTheDocument();

    // Attempting second click should not trigger another deleteAlias call
    fireEvent.click(confirmDeleteBtn);
    expect(deleteSpy).toHaveBeenCalledTimes(1);
    expect(deleteSpy).toHaveBeenCalledWith('192.168.1.1');

    // Resolve deletion
    resolveDelete!({ status: 'deleted', ip: '192.168.1.1' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('renders mobile touch cards when viewport is under 768px', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query.includes('max-width: 767px'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    render(<HostAliasManager />);

    await waitFor(() => {
      expect(screen.getByText('Active Host Mappings (2)')).toBeInTheDocument();
    });

    // In mobile view, the table header "SOURCE IP" is omitted
    expect(screen.queryByText('SOURCE IP')).not.toBeInTheDocument();

    // Mobile cards should render IP and alias
    expect(screen.getByText('192.168.1.1')).toBeInTheDocument();
    expect(screen.getByText('router.local')).toBeInTheDocument();
    expect(screen.getByText('Main router')).toBeInTheDocument();

    // Edit and Delete buttons should be present
    expect(screen.getAllByTitle('Edit Alias').length).toBe(2);
    expect(screen.getAllByTitle('Delete Alias').length).toBe(2);
  });
});
