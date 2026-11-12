import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DeleteSelectedDialog } from '../components/logs/DeleteSelectedDialog.tsx';

describe('DeleteSelectedDialog', () => {
  it('names how many logs it deletes and cancels without deleting', () => {
    const onClose = vi.fn();
    const onDelete = vi.fn();
    render(<DeleteSelectedDialog count={3} onClose={onClose} onDelete={onDelete} />);
    expect(screen.getByRole('dialog', { name: 'Delete Selected Logs' })).toHaveTextContent(
      'Permanently delete 3 selected log records? This action cannot be reversed.'
    );
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('says "record" for one log', () => {
    render(<DeleteSelectedDialog count={1} onClose={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByRole('dialog')).toHaveTextContent('Permanently delete 1 selected log record?');
  });

  it('locks both buttons and the close while deleting, then shows a failure in the dialog', async () => {
    let fail: (err: Error) => void = () => {};
    const onDelete = vi.fn(() => new Promise<void>((_, reject) => { fail = reject; }));
    const onClose = vi.fn();
    render(<DeleteSelectedDialog count={2} onClose={onClose} onDelete={onDelete} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Confirm Delete' }));
    });
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Deleting...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      fail(new Error('Database is locked'));
    });
    expect(screen.getByText('Database is locked')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm Delete' })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
