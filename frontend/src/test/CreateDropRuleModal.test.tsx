import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CreateDropRuleModal } from '../components/alerts/CreateDropRuleModal.tsx';
import { DropRule } from '../types.ts';

const mockRule: DropRule = {
  id: 10,
  name: 'Drop Healthchecks',
  source_pattern: '10.0.0.*',
  app_pattern: 'nginx',
  message_pattern: 'GET /healthz',
  is_regex: false,
  is_enabled: true,
  severity_threshold: null,
  dropped_count: 5,
  created_at: '2026-09-18T10:00:00Z',
};

describe('CreateDropRuleModal Component', () => {
  const onClose = vi.fn();
  const onSuccess = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('closes immediately without warning when X button is clicked with no unsaved changes', () => {
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={onClose}
        onSuccess={onSuccess}
        ruleToEdit={mockRule}
      />
    );

    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    expect(screen.queryByText('Unsaved Changes')).toBeNull();
    expect(onClose).toHaveBeenCalled();
  });

  it('warns about unsaved changes when user edits rule name and clicks X button', () => {
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={onClose}
        onSuccess={onSuccess}
        ruleToEdit={mockRule}
      />
    );

    const nameInput = screen.getByDisplayValue('Drop Healthchecks');
    fireEvent.change(nameInput, { target: { value: 'Drop All Healthchecks' } });

    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    expect(
      screen.getByText('You have unsaved changes. Closing now will discard those edits.')
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('keeps editing when "Keep Editing" is clicked in unsaved changes confirmation', () => {
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={onClose}
        onSuccess={onSuccess}
        ruleToEdit={mockRule}
      />
    );

    const nameInput = screen.getByDisplayValue('Drop Healthchecks');
    fireEvent.change(nameInput, { target: { value: 'Drop All Healthchecks' } });

    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();

    const keepEditingBtn = screen.getByRole('button', { name: 'Keep Editing' });
    fireEvent.click(keepEditingBtn);

    expect(screen.queryByText('Unsaved Changes')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue('Drop All Healthchecks')).toBeInTheDocument();
  });

  it('discards edits and closes when "Discard Changes" is clicked in unsaved changes confirmation', () => {
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={onClose}
        onSuccess={onSuccess}
        ruleToEdit={mockRule}
      />
    );

    const nameInput = screen.getByDisplayValue('Drop Healthchecks');
    fireEvent.change(nameInput, { target: { value: 'Drop All Healthchecks' } });

    const closeBtn = screen.getByRole('button', { name: 'Close dialog' });
    fireEvent.click(closeBtn);

    const discardBtn = screen.getByRole('button', { name: 'Discard Changes' });
    fireEvent.click(discardBtn);

    expect(screen.queryByText('Unsaved Changes')).toBeNull();
    expect(onClose).toHaveBeenCalled();
  });

  it('warns about unsaved changes when user clicks Cancel button with edits', () => {
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={onClose}
        onSuccess={onSuccess}
        ruleToEdit={mockRule}
      />
    );

    const nameInput = screen.getByDisplayValue('Drop Healthchecks');
    fireEvent.change(nameInput, { target: { value: 'Drop All Healthchecks' } });

    const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
    fireEvent.click(cancelBtn);

    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
