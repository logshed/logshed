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
    vi.clearAllMocks();
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

  it('links the drop rules guide to the Ingestion Drop Rules anchor', () => {
    render(<CreateDropRuleModal isOpen={true} onClose={onClose} onSuccess={onSuccess} ruleToEdit={null} />);
    const link = screen.getByTitle('Open Drop Rules Documentation');
    expect(link.getAttribute('href')).toMatch(/RULES_GUIDE\.md#ingestion-drop-rules$/);
  });
});

describe('CreateDropRuleModal severity range and whole-line hint', () => {
  const HINT = /remove numbers, times or IDs that change between lines/i;

  it('uses "less severe" wording and help text for the severity range', () => {
    render(<CreateDropRuleModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} ruleToEdit={null} />);
    expect(screen.getByLabelText('Severity Range (Optional)')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Warning (4) and less severe' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Debug (7) and less severe' })).toBeInTheDocument();
    expect(
      screen.getByText(
        'Only logs at this level or less severe are dropped. More severe logs are always kept. Leave blank to match every level.'
      )
    ).toBeInTheDocument();
  });

  it('shows the hint when the pattern is the whole sample line and hides it once edited', () => {
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        ruleToEdit={null}
        initialMessage="DHCPACK(eth0) 10.0.0.5 aa:bb"
      />
    );
    expect(screen.getByText(HINT)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. DHCPACK, query/), {
      target: { value: 'DHCPACK' },
    });
    expect(screen.queryByText(HINT)).toBeNull();
  });

  it('does not show the hint without a pattern or when editing', () => {
    const { unmount } = render(
      <CreateDropRuleModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} ruleToEdit={null} />
    );
    expect(screen.queryByText(HINT)).toBeNull();
    unmount();
    render(<CreateDropRuleModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} ruleToEdit={mockRule} />);
    expect(screen.queryByText(HINT)).toBeNull();
  });
});

describe('CreateDropRuleModal message pattern length', () => {
  it('accepts a message pattern as long as the backend allows', () => {
    const longPattern = 'x'.repeat(1000);
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        ruleToEdit={{ ...mockRule, message_pattern: longPattern }}
      />
    );
    const input = screen.getByPlaceholderText(/DHCPACK/);
    expect(input).toHaveAttribute('maxLength', '1000');
    expect(input).toHaveValue(longPattern);
  });
});
