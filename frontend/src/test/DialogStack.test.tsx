import { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { Modal } from '../components/common/Modal.tsx';
import { SlideOver } from '../components/common/SlideOver.tsx';
import { MultiSelectDropdown } from '../components/common/MultiSelectDropdown.tsx';
import { openDialogCount } from '../utils/dialogStack.ts';

const pressEscape = () => fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
const pressTab = (shiftKey = false) => fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Tab', shiftKey });

/** A page with a button that opens a drawer, which has a button that opens a modal on top. */
function NestedDialogs({ onDrawerClose, onModalClose }: { onDrawerClose?: () => void; onModalClose?: () => void }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  return (
    <>
      <button onClick={() => setDrawerOpen(true)}>Open drawer</button>
      <SlideOver
        isOpen={drawerOpen}
        onClose={() => {
          onDrawerClose?.();
          setDrawerOpen(false);
        }}
        title="Log drawer"
      >
        <button onClick={() => setModalOpen(true)}>Open modal</button>
        <Modal
          isOpen={modalOpen}
          onClose={() => {
            onModalClose?.();
            setModalOpen(false);
          }}
          title="Confirm delete"
        >
          <button>Cancel</button>
          <button>Delete</button>
        </Modal>
      </SlideOver>
    </>
  );
}

describe('Dialogs (Modal, SlideOver and the dialog stack)', () => {
  it('names Modal and SlideOver dialogs after their titles', () => {
    render(
      <>
        <Modal isOpen onClose={vi.fn()} title="Rename Saved View">
          <p>Body</p>
        </Modal>
        <SlideOver isOpen onClose={vi.fn()} title="Filter Logs">
          <p>Drawer</p>
        </SlideOver>
      </>,
    );
    const modal = screen.getByRole('dialog', { name: 'Rename Saved View' });
    expect(modal).toHaveAttribute('aria-modal', 'true');
    const drawer = screen.getByRole('dialog', { name: 'Filter Logs' });
    expect(drawer).toHaveAttribute('aria-modal', 'true');
  });

  it('moves focus into the dialog on open and back to the opener on close', () => {
    function Page() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open</button>
          <Modal isOpen={open} onClose={() => setOpen(false)} title="Settings">
            <button>Inside</button>
          </Modal>
        </>
      );
    }
    render(<Page />);
    const opener = screen.getByRole('button', { name: 'Open' });
    opener.focus();
    fireEvent.click(opener);

    expect(screen.getByRole('dialog', { name: 'Settings' })).toHaveFocus();
    pressEscape();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(opener).toHaveFocus();
  });

  it('keeps a field that took focus itself (autoFocus) focused, and focuses a data-autofocus element otherwise', () => {
    const { unmount } = render(
      <Modal isOpen onClose={vi.fn()} title="Name">
        <input aria-label="Name field" autoFocus />
      </Modal>,
    );
    expect(screen.getByLabelText('Name field')).toHaveFocus();
    unmount();

    render(
      <Modal isOpen onClose={vi.fn()} title="Delete">
        <button>Cancel</button>
        <button data-autofocus>Delete</button>
      </Modal>,
    );
    expect(screen.getByRole('button', { name: 'Delete' })).toHaveFocus();
  });

  it('keeps Tab and Shift+Tab inside the open dialog', () => {
    render(
      <>
        <button>Outside</button>
        <Modal isOpen onClose={vi.fn()} title="Trap">
          <button>First</button>
          <button>Last</button>
        </Modal>
      </>,
    );
    const close = screen.getByRole('button', { name: 'Close dialog' });
    const last = screen.getByRole('button', { name: 'Last' });

    last.focus();
    pressTab();
    expect(close).toHaveFocus();

    pressTab(true);
    expect(last).toHaveFocus();

    // Focus that escaped (for example a click outside) is brought back in
    screen.getByRole('button', { name: 'Outside' }).focus();
    pressTab();
    expect(close).toHaveFocus();
  });

  it('closes only the top dialog on Esc, then the one below on the next Esc', () => {
    const onDrawerClose = vi.fn();
    const onModalClose = vi.fn();
    render(<NestedDialogs onDrawerClose={onDrawerClose} onModalClose={onModalClose} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open drawer' }));
    const openModal = screen.getByRole('button', { name: 'Open modal' });
    openModal.focus();
    fireEvent.click(openModal);
    expect(openDialogCount()).toBe(2);

    pressEscape();
    expect(onModalClose).toHaveBeenCalledTimes(1);
    expect(onDrawerClose).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: 'Confirm delete' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Log drawer' })).toBeInTheDocument();
    // Focus returns to the button in the drawer that opened the modal
    expect(openModal).toHaveFocus();

    pressEscape();
    expect(onDrawerClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(openDialogCount()).toBe(0);
  });

  it('traps Tab in the top dialog only', () => {
    render(<NestedDialogs />);
    fireEvent.click(screen.getByRole('button', { name: 'Open drawer' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open modal' }));
    const modal = screen.getByRole('dialog', { name: 'Confirm delete' });
    const deleteButton = screen.getByRole('button', { name: 'Delete' });
    deleteButton.focus();
    pressTab();
    expect(modal.contains(document.activeElement)).toBe(true);
    expect(screen.getByRole('button', { name: 'Close dialog' })).toHaveFocus();
  });

  it('lets an open dropdown inside a dialog take Esc first', () => {
    const onClose = vi.fn();
    render(
      <Modal isOpen onClose={onClose} title="Rule">
        <MultiSelectDropdown label="Apps" options={['nginx', 'sshd']} selected={[]} onChange={vi.fn()} />
      </Modal>,
    );
    fireEvent.click(screen.getByRole('button', { name: /Apps/ }));
    expect(screen.getByText('sshd')).toBeInTheDocument();

    pressEscape();
    expect(screen.queryByText('sshd')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();

    pressEscape();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('returns focus to the first opener when a dialog replaced the one it was opened from', () => {
    // As Create Drop Rule in the log drawer: the drawer closes and a dialog with an autoFocus field opens
    function Page() {
      const [drawerOpen, setDrawerOpen] = useState(false);
      const [ruleOpen, setRuleOpen] = useState(false);
      return (
        <>
          <button onClick={() => setDrawerOpen(true)}>Open log</button>
          <SlideOver isOpen={drawerOpen} onClose={() => setDrawerOpen(false)} title="Log drawer">
            <button
              onClick={() => {
                setDrawerOpen(false);
                setRuleOpen(true);
              }}
            >
              Create rule
            </button>
          </SlideOver>
          <Modal isOpen={ruleOpen} onClose={() => setRuleOpen(false)} title="New rule">
            <input aria-label="Rule name" autoFocus />
          </Modal>
        </>
      );
    }
    render(<Page />);
    const openLog = screen.getByRole('button', { name: 'Open log' });
    openLog.focus();
    fireEvent.click(openLog);
    const createRule = screen.getByRole('button', { name: 'Create rule' });
    createRule.focus();
    fireEvent.click(createRule);
    expect(screen.queryByRole('dialog', { name: 'Log drawer' })).toBeNull();
    expect(screen.getByLabelText('Rule name')).toHaveFocus();

    pressEscape();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(openLog).toHaveFocus();
  });

  it('leaves the stack empty when a dialog unmounts while open', () => {
    const { unmount } = render(
      <Modal isOpen onClose={vi.fn()} title="Gone">
        <p>x</p>
      </Modal>,
    );
    expect(openDialogCount()).toBe(1);
    unmount();
    expect(openDialogCount()).toBe(0);
  });
});
