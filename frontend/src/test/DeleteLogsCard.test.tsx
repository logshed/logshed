import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { DeleteLogsCard } from '../components/storage/DeleteLogsCard.tsx';
import * as logsApi from '../api/logs.ts';
import * as settingsApi from '../api/settings.ts';

describe('DeleteLogsCard Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(logsApi, 'fetchLogFacets').mockResolvedValue({
      sources: ['server-1', 'server-2'],
      apps: ['nginx', 'postgres'],
      host_to_apps: {},
      app_to_hosts: {},
    });
  });

  it('renders card with None defaults and does not target all logs initially', async () => {
    render(<DeleteLogsCard />);
    // Let the host and app lists load
    await act(async () => {});

    expect(screen.getByRole('heading', { name: /Targeted Log Deletion/i })).toBeInTheDocument();
    expect(screen.getByText(/Host \/ IP \(Optional\)/i)).toBeInTheDocument();
    expect(screen.getByText(/App \/ Container \(Optional\)/i)).toBeInTheDocument();
    expect(screen.getByText('All Time')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/e\.g\. timeout, connection reset, error/i)).toBeInTheDocument();

    // Default dropdown titles should be "None"
    const noneElements = screen.getAllByTitle('None');
    expect(noneElements.length).toBeGreaterThanOrEqual(2);

    // Initial state should indicate no criteria selected and no high-risk warning
    expect(screen.getByText(/Select criteria above to preview logs targeted for deletion/i)).toBeInTheDocument();
    expect(screen.getByText(/No criteria selected/i)).toBeInTheDocument();
    expect(screen.queryByText(/High Risk Operation Confirmation/i)).not.toBeInTheDocument();

    const deleteBtn = screen.getByRole('button', { name: /Delete Matching Logs/i });
    expect(deleteBtn).toBeDisabled();
  });

  it('updates preview count when a host filter is selected', async () => {
    const previewSpy = vi.spyOn(logsApi, 'previewDeleteLogs')
      .mockResolvedValueOnce({ matched_count: 25 });

    render(<DeleteLogsCard />);

    // Open Host dropdown
    const hostTrigger = screen.getAllByTitle('None')[0];
    fireEvent.click(hostTrigger);

    // Click 'server-1' option
    const option = await screen.findByText('server-1');
    fireEvent.click(option);

    await waitFor(() => {
      expect(screen.getByTestId('matched-count-display')).toHaveTextContent('25');
    });

    expect(previewSpy).toHaveBeenCalledWith(expect.objectContaining({
      sources: ['server-1'],
    }));
  });

  it('enforces typed confirmation for high risk / all logs and deletes successfully', async () => {
    vi.spyOn(logsApi, 'previewDeleteLogs').mockResolvedValue({ matched_count: 800 });
    const deleteSpy = vi.spyOn(logsApi, 'deleteLogs').mockResolvedValue({
      status: 'ok',
      deleted_count: 800,
      message: 'Deleted 800 logs.',
    });
    const onLogsDeletedMock = vi.fn();

    render(<DeleteLogsCard onLogsDeleted={onLogsDeletedMock} />);

    // Click 'All Time' to target all logs
    const allTimeBtn = screen.getByRole('button', { name: 'All Time' });
    fireEvent.click(allTimeBtn);

    await waitFor(() => {
      expect(screen.getByTestId('matched-count-display')).toHaveTextContent('800');
    });

    // High risk banner must appear
    expect(screen.getByText(/High Risk Operation Confirmation/i)).toBeInTheDocument();

    const deleteBtn = screen.getByRole('button', { name: /Delete Matching Logs/i });
    expect(deleteBtn).toBeDisabled();

    // Type incorrect confirmation
    const confirmInput = screen.getByPlaceholderText('Type DELETE to confirm');
    fireEvent.change(confirmInput, { target: { value: 'WRONG' } });
    expect(deleteBtn).toBeDisabled();

    // Type correct confirmation
    fireEvent.change(confirmInput, { target: { value: 'DELETE' } });
    expect(deleteBtn).not.toBeDisabled();

    // Click Delete
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith(expect.objectContaining({
        delete_all: true,
      }));
      expect(onLogsDeletedMock).toHaveBeenCalled();
      expect(screen.getByText('Successfully deleted 800 logs.')).toBeInTheDocument();
    });
  });

  it('resets criteria back to defaults when Reset Criteria is clicked', async () => {
    vi.spyOn(logsApi, 'previewDeleteLogs').mockResolvedValue({ matched_count: 10 });

    render(<DeleteLogsCard />);
    // Let the host and app lists load
    await act(async () => {});

    // Change time preset to '7d'
    const sevenDayBtn = screen.getByRole('button', { name: 'Past 7 Days' });
    fireEvent.click(sevenDayBtn);

    const queryInput = screen.getByPlaceholderText(/e\.g\. timeout, connection reset, error/i);
    fireEvent.change(queryInput, { target: { value: 'fatal exception' } });

    expect(queryInput).toHaveValue('fatal exception');

    const resetBtn = screen.getByRole('button', { name: /Reset Criteria/i });
    fireEvent.click(resetBtn);

    expect(queryInput).toHaveValue('');
    expect(screen.getByText(/Select criteria above to preview logs targeted for deletion/i)).toBeInTheDocument();
  });

  it('reads a custom range in the server time zone, not the browser one', async () => {
    const originalTz = process.env.TZ;
    process.env.TZ = 'America/New_York';
    vi.spyOn(settingsApi, 'getCachedServerTimezone').mockReturnValue('Pacific/Kiritimati');
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ server_timezone: 'Pacific/Kiritimati' } as any);
    const previewSpy = vi.spyOn(logsApi, 'previewDeleteLogs').mockResolvedValue({ matched_count: 3 });
    const deleteSpy = vi.spyOn(logsApi, 'deleteLogs').mockResolvedValue({
      status: 'ok',
      deleted_count: 3,
      message: 'Deleted 3 logs.',
    });
    try {
      const { container } = render(<DeleteLogsCard />);
      await act(async () => {});

      fireEvent.click(screen.getByRole('button', { name: 'Custom Range' }));
      const [fromInput, toInput] = Array.from(container.querySelectorAll('input[type="datetime-local"]'));
      // Kiritimati is UTC+14 all year
      fireEvent.change(fromInput, { target: { value: '2026-10-13T10:00' } });
      fireEvent.change(toInput, { target: { value: '2026-10-13T12:30' } });

      await waitFor(() => {
        expect(screen.getByTestId('matched-count-display')).toHaveTextContent('3');
      });
      expect(previewSpy).toHaveBeenLastCalledWith(expect.objectContaining({
        from: '2026-10-12T20:00:00.000Z',
        to: '2026-10-12T22:30:00.000Z',
      }));

      fireEvent.click(screen.getByRole('button', { name: /Delete Matching Logs/i }));
      await waitFor(() => expect(deleteSpy).toHaveBeenCalled());
      expect(deleteSpy).toHaveBeenCalledWith(expect.objectContaining({
        from: '2026-10-12T20:00:00.000Z',
        to: '2026-10-12T22:30:00.000Z',
      }));
      await waitFor(() => expect(screen.getByText('Successfully deleted 3 logs.')).toBeInTheDocument());
      // The fields say which zone they are read in
      expect(fromInput).toHaveAccessibleName('From (Pacific/Kiritimati)');
      expect(toInput).toHaveAccessibleName('To (Pacific/Kiritimati)');
    } finally {
      process.env.TZ = originalTz;
    }
  });
});
