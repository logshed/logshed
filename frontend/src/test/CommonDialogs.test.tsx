import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { ConfirmDialog } from '../components/common/ConfirmDialog.tsx';
import { CopyButton } from '../components/common/CopyButton.tsx';
import { UnsavedChangesModal } from '../components/common/UnsavedChangesModal.tsx';
import * as clipboardModule from '../utils/clipboard.ts';

describe('ConfirmDialog', () => {
  it('shows the question and calls cancel or confirm', () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog isOpen title="Delete Thing" onCancel={onCancel} onConfirm={onConfirm} confirmLabel="Delete">
        <p>Delete this thing?</p>
      </ConfirmDialog>
    );
    expect(screen.getByRole('dialog', { name: 'Delete Thing' })).toBeInTheDocument();
    expect(screen.getByText('Delete this thing?')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('closes through onCancel, or through onClose when one is given', () => {
    const onCancel = vi.fn();
    const onClose = vi.fn();
    const { rerender } = render(
      <ConfirmDialog isOpen title="Delete Thing" onCancel={onCancel} onConfirm={vi.fn()} confirmLabel="Delete">
        <p>Body</p>
      </ConfirmDialog>
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(onCancel).toHaveBeenCalledTimes(1);

    rerender(
      <ConfirmDialog isOpen title="Delete Thing" onCancel={onCancel} onClose={onClose} onConfirm={vi.fn()} confirmLabel="Delete">
        <p>Body</p>
      </ConfirmDialog>
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('disables the buttons it is told to, and renders nothing while closed', () => {
    const { rerender } = render(
      <ConfirmDialog
        isOpen
        title="Remove Host"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
        confirmLabel="Removing..."
        cancelDisabled
        confirmDisabled
      >
        <p>Body</p>
      </ConfirmDialog>
    );
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Removing...' })).toBeDisabled();

    rerender(
      <ConfirmDialog isOpen={false} title="Remove Host" onCancel={vi.fn()} onConfirm={vi.fn()} confirmLabel="Remove">
        <p>Body</p>
      </ConfirmDialog>
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('puts the notice look inside a warning box and the icon before the label', () => {
    render(
      <ConfirmDialog
        isOpen
        title="Delete View"
        look="notice"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
        confirmIcon={<svg data-testid="confirm-icon" />}
        confirmLabel="Delete View"
      >
        <p>Delete this view?</p>
      </ConfirmDialog>
    );
    const box = screen.getByText('Delete this view?').parentElement!.parentElement!;
    expect(box.className).toContain('bg-danger-950/30');
    const confirm = screen.getByRole('button', { name: 'Delete View' });
    expect(confirm.firstElementChild).toBe(screen.getByTestId('confirm-icon'));
    expect(confirm.querySelector('span')).toHaveTextContent('Delete View');
  });
});

describe('CopyButton', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('copies its text and shows Copied for a while', async () => {
    vi.useFakeTimers();
    const copySpy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
    render(<CopyButton text="hello" label="Copy Message" title="Copy message payload" />);

    const button = screen.getByTitle('Copy message payload');
    expect(button).toHaveTextContent('Copy Message');
    await act(async () => {
      fireEvent.click(button);
    });
    expect(copySpy).toHaveBeenCalledWith('hello');
    expect(button).toHaveTextContent('Copied');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(button).toHaveTextContent('Copy Message');
  });

  it('reads a text function at click time, runs beforeCopy, and copies nothing when empty', async () => {
    const copySpy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
    const beforeCopy = vi.fn();
    let value = '';
    render(<CopyButton text={() => value} beforeCopy={beforeCopy} copiedLabelClassName="text-success-400" />);
    const button = screen.getByRole('button', { name: 'Copy' });

    await act(async () => {
      fireEvent.click(button);
    });
    expect(copySpy).not.toHaveBeenCalled();
    expect(beforeCopy).not.toHaveBeenCalled();

    value = 'token';
    await act(async () => {
      fireEvent.click(button);
    });
    expect(beforeCopy).toHaveBeenCalledTimes(1);
    expect(copySpy).toHaveBeenCalledWith('token');
    expect(screen.getByText('Copied')).toHaveClass('text-success-400');
  });

  it('keeps showing Copy when the copy fails', async () => {
    vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(false);
    render(<CopyButton text="hello" />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    });
    expect(screen.queryByText('Copied')).not.toBeInTheDocument();
  });
});

describe('UnsavedChangesModal', () => {
  it('has no save button unless onSave is given', () => {
    render(<UnsavedChangesModal isOpen onKeepEditing={vi.fn()} onDiscard={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Discard Changes' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Save & Continue/ })).not.toBeInTheDocument();
  });

  it('disables Save & Continue while the save runs', async () => {
    let finishSave: () => void = () => {};
    const onSave = vi.fn(() => new Promise<void>((resolve) => { finishSave = resolve; }));
    render(
      <UnsavedChangesModal isOpen onKeepEditing={vi.fn()} onDiscard={vi.fn()} onSave={onSave} discardLabel="Discard & Leave" />
    );
    expect(screen.getByRole('button', { name: 'Discard & Leave' })).toBeInTheDocument();
    const save = screen.getByRole('button', { name: /Save & Continue/ });

    await act(async () => {
      fireEvent.click(save);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(save).toBeDisabled();

    await act(async () => {
      finishSave();
    });
    expect(save).not.toBeDisabled();
  });
});
