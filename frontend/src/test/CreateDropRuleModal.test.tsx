import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CreateDropRuleModal } from '../components/alerts/CreateDropRuleModal.tsx';
import { splitPatternList } from '../utils/patternList.ts';
import * as dropRulesApi from '../api/dropRules.ts';
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

describe('CreateDropRuleModal host and app lists', () => {
  const hostTrigger = () => screen.getByRole('button', { name: /^Hosts or IPs/ });
  const appTrigger = () => screen.getByRole('button', { name: /^Apps or Containers/ });

  const toggleOption = (option: string) => {
    const row = screen.getAllByRole('button', { name: option }).find((el) => el.hasAttribute('aria-pressed'));
    fireEvent.click(row!);
  };
  const typePattern = (placeholder: string, value: string) => {
    const search = screen.getByPlaceholderText(placeholder);
    fireEvent.change(search, { target: { value } });
    fireEvent.keyDown(search, { key: 'Enter' });
  };
  const closeDropdown = () => fireEvent.click(screen.getByTestId('dropdown-backdrop'));

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('creates a rule for several hosts and apps, from the lists and typed patterns', async () => {
    const createSpy = vi.spyOn(dropRulesApi, 'createDropRule').mockResolvedValue({ ...mockRule, id: 11 });
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        availableSources={['pve-node1', 'pve-node2', 'router']}
        availableApps={['sshd', 'cron', 'nginx']}
      />
    );
    fireEvent.change(screen.getByPlaceholderText(/CRON Chatter/), { target: { value: 'Session noise' } });

    fireEvent.click(hostTrigger());
    // The open list is inside the dialog, so screen readers and the dialog's Tab trap reach it
    expect(screen.getByRole('dialog')).toContainElement(screen.getByPlaceholderText('Search or add host...'));
    toggleOption('pve-node1');
    toggleOption('pve-node2');
    typePattern('Search or add host...', '192.168.1.*');
    closeDropdown();

    fireEvent.click(appTrigger());
    toggleOption('sshd');
    typePattern('Search or add app...', 'cron*');
    closeDropdown();

    // The trigger names the field and lists the selection
    expect(hostTrigger()).toHaveAccessibleName('Hosts or IPs (Optional) pve-node1, pve-node2, 192.168.1.* 3 selected');
    expect(appTrigger()).toHaveAccessibleName('Apps or Containers (Optional) sshd, cron* 2 selected');
    // The sample host and app take the first entry
    expect(screen.getByPlaceholderText('e.g. 192.168.1.50')).toHaveValue('pve-node1');
    expect(screen.getByPlaceholderText('e.g. dnsmasq')).toHaveValue('sshd');

    fireEvent.click(screen.getByRole('button', { name: 'Create Rule' }));
    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Session noise',
          source_pattern: 'pve-node1, pve-node2, 192.168.1.*',
          app_pattern: 'sshd, cron*',
        })
      )
    );
  });

  it('opens a single-pattern rule as one entry and saves it unchanged', async () => {
    const onClose = vi.fn();
    const updateSpy = vi.spyOn(dropRulesApi, 'updateDropRule').mockResolvedValue(mockRule);
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={onClose}
        onSuccess={vi.fn()}
        ruleToEdit={mockRule}
        availableSources={['router']}
        availableApps={['nginx', 'sshd']}
      />
    );
    expect(hostTrigger()).toHaveAccessibleName('Hosts or IPs (Optional) 10.0.0.*');
    expect(appTrigger()).toHaveAccessibleName('Apps or Containers (Optional) nginx');

    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(10, expect.objectContaining({ source_pattern: '10.0.0.*', app_pattern: 'nginx' }))
    );
  });

  it('edits a rule with lists: removing one entry saves the rest, and closing asks first', async () => {
    const onClose = vi.fn();
    const updateSpy = vi.spyOn(dropRulesApi, 'updateDropRule').mockResolvedValue(mockRule);
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={onClose}
        onSuccess={vi.fn()}
        ruleToEdit={{ ...mockRule, source_pattern: 'web-01, web-02', app_pattern: 'nginx, php-fpm' }}
        availableSources={['web-01']}
        availableApps={['nginx']}
      />
    );
    expect(hostTrigger()).toHaveAccessibleName('Hosts or IPs (Optional) web-01, web-02 2 selected');
    // Unchanged lists are not unsaved changes
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(appTrigger());
    toggleOption('nginx');
    closeDropdown();
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep Editing' }));

    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(
        10,
        expect.objectContaining({ source_pattern: 'web-01, web-02', app_pattern: 'php-fpm' })
      )
    );
  });

  it('clears a list with the button beside the trigger, not inside it', async () => {
    const updateSpy = vi.spyOn(dropRulesApi, 'updateDropRule').mockResolvedValue(mockRule);
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        ruleToEdit={{ ...mockRule, source_pattern: 'web-01, web-02' }}
      />
    );
    const clearButtons = screen.getAllByRole('button', { name: 'Clear selection' });
    expect(clearButtons).toHaveLength(2);
    for (const button of clearButtons) {
      expect(button.closest('[role="button"]')).toBeNull();
    }
    fireEvent.click(clearButtons[0]);
    expect(hostTrigger()).toHaveAccessibleName('Hosts or IPs (Optional) Any host or IP');
    // Clearing does not open the dropdown
    expect(screen.queryByTestId('dropdown-backdrop')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(10, expect.objectContaining({ source_pattern: null, app_pattern: 'nginx' }))
    );
  });

  it('starts from the one host and app of the log it was opened from', () => {
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        initialSource="pve-node1"
        initialApp="pvedaemon"
        availableSources={['pve-node1']}
        availableApps={[]}
      />
    );
    expect(hostTrigger()).toHaveAccessibleName('Hosts or IPs (Optional) pve-node1');
    expect(appTrigger()).toHaveAccessibleName('Apps or Containers (Optional) pvedaemon');
    expect(screen.getByDisplayValue('Drop pvedaemon Chatter')).toBeInTheDocument();
  });

  it('refuses a list longer than the backend allows', () => {
    const apps = Array.from({ length: 12 }, (_, i) => `container-number-${String(i).padStart(2, '0')}`);
    render(
      <CreateDropRuleModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        ruleToEdit={{ ...mockRule, app_pattern: apps.join(', ') }}
      />
    );
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(appTrigger());
    typePattern('Search or add app...', 'one-more-container-name');
    closeDropdown();
    expect(screen.getByRole('alert')).toHaveTextContent('The app list is longer than 255 characters');
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  });
});

describe('splitPatternList', () => {
  it('trims, drops empty parts and repeats in another case', () => {
    expect(splitPatternList(' sshd ,CRON,, cron , ngin* ')).toEqual(['sshd', 'CRON', 'ngin*']);
    expect(splitPatternList(null)).toEqual([]);
    expect(splitPatternList(' , ')).toEqual([]);
  });
});
