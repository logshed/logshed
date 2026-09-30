import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DeleteLogsModal } from '../components/logs/DeleteLogsModal.tsx';
import * as logsApi from '../api/logs.ts';

describe('DeleteLogsModal Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders modal with host, app, time, and search controls', async () => {
    vi.spyOn(logsApi, 'previewDeleteLogs').mockResolvedValue({ matched_count: 42 });

    render(
      <DeleteLogsModal
        isOpen={true}
        onClose={vi.fn()}
        availableSources={['host-1', 'host-2']}
        availableApps={['nginx', 'postgres']}
      />
    );

    expect(screen.getByRole('heading', { name: 'Delete Logs' })).toBeInTheDocument();
    expect(screen.getByText(/Host \/ IP \(Optional\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Application \/ Container \(Optional\)/i)).toBeInTheDocument();
    expect(screen.getByText('All Time')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/error, connection refused, kernel/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId('matched-count-display')).toHaveTextContent('42');
    });
  });

  it('updates preview count when filters change', async () => {
    const previewSpy = vi.spyOn(logsApi, 'previewDeleteLogs')
      .mockResolvedValueOnce({ matched_count: 100 })
      .mockResolvedValueOnce({ matched_count: 15 });

    render(
      <DeleteLogsModal
        isOpen={true}
        onClose={vi.fn()}
        availableSources={['pve1', 'pve2']}
        availableApps={['nginx', 'sshd']}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('matched-count-display')).toHaveTextContent('100');
    });

    // Open Host dropdown
    const hostTrigger = screen.getAllByTitle('None')[0];
    fireEvent.click(hostTrigger);

    // Click 'pve1' option
    const option = await screen.findByText('pve1');
    fireEvent.click(option);

    await waitFor(() => {
      expect(screen.getByTestId('matched-count-display')).toHaveTextContent('15');
    });

    expect(previewSpy).toHaveBeenCalledWith(expect.objectContaining({
      sources: ['pve1'],
    }));
  });

  it('enforces typed confirmation when targeting high risk / all logs', async () => {
    vi.spyOn(logsApi, 'previewDeleteLogs').mockResolvedValue({ matched_count: 1200 });
    const deleteSpy = vi.spyOn(logsApi, 'deleteLogs').mockResolvedValue({
      status: 'ok',
      deleted_count: 1200,
      message: 'Deleted 1200 logs.',
    });
    const onDeletedMock = vi.fn();
    const onCloseMock = vi.fn();

    render(
      <DeleteLogsModal
        isOpen={true}
        onClose={onCloseMock}
        onDeleted={onDeletedMock}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('matched-count-display')).toHaveTextContent('1,200');
    });

    // High risk warning must be displayed
    expect(screen.getByText(/High Risk Operation Confirmation/i)).toBeInTheDocument();

    const deleteBtn = screen.getByRole('button', { name: /Delete Matching Logs/i });
    expect(deleteBtn).toBeDisabled();

    // Type incorrect confirmation text
    const confirmInput = screen.getByPlaceholderText('Type DELETE to confirm');
    fireEvent.change(confirmInput, { target: { value: 'WRONG' } });
    expect(deleteBtn).toBeDisabled();

    // Type correct confirmation text
    fireEvent.change(confirmInput, { target: { value: 'DELETE' } });
    expect(deleteBtn).not.toBeDisabled();

    // Click Delete
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith(expect.objectContaining({
        delete_all: true,
      }));
      expect(onDeletedMock).toHaveBeenCalledWith(1200);
      expect(onCloseMock).toHaveBeenCalled();
    });
  });

  it('allows immediate deletion when count is low and scope is targeted', async () => {
    vi.spyOn(logsApi, 'previewDeleteLogs').mockResolvedValue({ matched_count: 5 });
    const deleteSpy = vi.spyOn(logsApi, 'deleteLogs').mockResolvedValue({
      status: 'ok',
      deleted_count: 5,
      message: 'Deleted 5 logs.',
    });
    const onDeletedMock = vi.fn();
    const onCloseMock = vi.fn();

    render(
      <DeleteLogsModal
        isOpen={true}
        onClose={onCloseMock}
        onDeleted={onDeletedMock}
        initialApp="nginx"
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('matched-count-display')).toHaveTextContent('5');
    });

    // No DELETE confirmation input needed for low-count targeted scope
    expect(screen.queryByPlaceholderText('Type DELETE to confirm')).not.toBeInTheDocument();

    const deleteBtn = screen.getByRole('button', { name: /Delete Matching Logs/i });
    expect(deleteBtn).not.toBeDisabled();

    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith(expect.objectContaining({
        apps: ['nginx'],
      }));
      expect(onDeletedMock).toHaveBeenCalledWith(5);
      expect(onCloseMock).toHaveBeenCalled();
    });
  });
});
