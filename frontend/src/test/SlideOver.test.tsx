import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { SlideOver } from '../components/common/SlideOver.tsx';

describe('SlideOver Component', () => {
  it('renders nothing when isOpen is false', () => {
    const handleClose = vi.fn();
    render(
      <SlideOver isOpen={false} onClose={handleClose} title="Test Drawer">
        <div>Drawer Content</div>
      </SlideOver>
    );

    expect(screen.queryByText('Test Drawer')).toBeNull();
    expect(screen.queryByText('Drawer Content')).toBeNull();
  });

  it('renders content when isOpen is true', () => {
    const handleClose = vi.fn();
    render(
      <SlideOver isOpen={true} onClose={handleClose} title="Test Drawer">
        <div>Drawer Content</div>
      </SlideOver>
    );

    expect(screen.getByText('Test Drawer')).toBeInTheDocument();
    expect(screen.getByText('Drawer Content')).toBeInTheDocument();
  });

  it('closes drawer when clicking on the backdrop overlay', () => {
    const handleClose = vi.fn();
    render(
      <SlideOver isOpen={true} onClose={handleClose} title="Test Drawer">
        <div>Drawer Content</div>
      </SlideOver>
    );

    const backdrop = screen.getByTestId('slideover-backdrop');
    expect(backdrop).toBeInTheDocument();

    fireEvent.click(backdrop);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('does not close drawer when clicking inside the drawer panel', () => {
    const handleClose = vi.fn();
    render(
      <SlideOver isOpen={true} onClose={handleClose} title="Test Drawer">
        <button>Inside Button</button>
      </SlideOver>
    );

    const insideButton = screen.getByText('Inside Button');
    fireEvent.click(insideButton);

    expect(handleClose).not.toHaveBeenCalled();
  });

  it('closes drawer when clicking the close (X) button', () => {
    const handleClose = vi.fn();
    render(
      <SlideOver isOpen={true} onClose={handleClose} title="Test Drawer">
        <div>Drawer Content</div>
      </SlideOver>
    );

    const closeBtn = screen.getByLabelText('Close panel');
    fireEvent.click(closeBtn);

    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it('closes drawer when pressing the Escape key', () => {
    const handleClose = vi.fn();
    render(
      <SlideOver isOpen={true} onClose={handleClose} title="Test Drawer">
        <div>Drawer Content</div>
      </SlideOver>
    );

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(handleClose).toHaveBeenCalledTimes(1);
  });
});
