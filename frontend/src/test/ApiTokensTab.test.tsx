import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ApiTokensTab, getPresetName } from '../components/settings/ApiTokensTab.tsx';
import * as tokensApi from '../api/tokens.ts';
import * as settingsApi from '../api/settings.ts';
import * as clipboardModule from '../utils/clipboard.ts';
import { ApiToken } from '../types.ts';

const mockTokens: ApiToken[] = [
  {
    id: 1,
    name: 'Backup Script',
    token_prefix: 'ls_live_a1b2...c3d4',
    scopes: ['maintenance:write', 'maintenance:read'],
    created_at: '2026-10-09T10:00:00Z',
    expires_at: '2027-01-07T10:00:00Z',
    last_used_at: '2026-10-09T10:15:00Z',
    created_by: 'admin',
  },
  {
    id: 2,
    name: 'Home Assistant',
    token_prefix: 'ls_live_e5f6...g7h8',
    scopes: ['*'],
    created_at: '2026-10-09T11:00:00Z',
    expires_at: null,
    last_used_at: null,
    created_by: 'admin',
  },
];

describe('ApiTokensTab Component', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders empty state when no tokens exist', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);

    render(<ApiTokensTab />);

    await waitFor(() => {
      expect(screen.getByText('No API tokens generated yet')).toBeInTheDocument();
    });

    expect(screen.getByText('External API Access')).toBeInTheDocument();
    expect(screen.getByText('Active Tokens (0)')).toBeInTheDocument();
  });

  it('renders active token table with formatted items', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue(mockTokens);

    render(<ApiTokensTab />);

    await waitFor(() => {
      expect(screen.getByText('Active Tokens (2)')).toBeInTheDocument();
    });

    expect(screen.getByText('Backup Script')).toBeInTheDocument();
    expect(screen.getByText('Home Assistant')).toBeInTheDocument();
    expect(screen.getByText('ls_live_a1b2...c3d4')).toBeInTheDocument();
    expect(screen.getByText('ls_live_e5f6...g7h8')).toBeInTheDocument();
    expect(screen.getByText('Maintenance Only')).toBeInTheDocument();
    expect(screen.getByText('Full Access')).toBeInTheDocument();
  });

  it('generates a new token and displays the one-time raw token modal', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue(mockTokens);
    const createSpy = vi.spyOn(tokensApi, 'createApiToken').mockResolvedValue({
      token: {
        id: 3,
        name: 'Cursor MCP',
        token_prefix: 'ls_live_9999...0000',
        scopes: ['logs:read', 'alerts:read', 'system:read'],
        created_at: '2026-10-09T12:00:00Z',
        expires_at: '2027-01-07T10:00:00Z',
        last_used_at: null,
      },
      raw_token: 'ls_live_9999888877776666555544443333222211110000',
    });

    render(<ApiTokensTab />);

    await waitFor(() => {
      expect(screen.getByText('Active Tokens (2)')).toBeInTheDocument();
    });

    // Click Generate Token
    const generateBtn = screen.getByRole('button', { name: /Generate Token/i });
    fireEvent.click(generateBtn);

    expect(screen.getByText('Generate API Token')).toBeInTheDocument();

    // Type token name
    const nameInput = screen.getByPlaceholderText('e.g. Home Assistant or Claude Desktop');
    fireEvent.change(nameInput, { target: { value: 'Cursor MCP' } });

    // Submit form
    const createBtn = screen.getByRole('button', { name: /Create Token/i });
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith({
        name: 'Cursor MCP',
        // The default MCP preset is read-only: maintenance:write must be chosen explicitly
        scopes: ['logs:read', 'alerts:read', 'system:read'],
        expires_days: 90,
      });
      expect(screen.getByText('Token Generated')).toBeInTheDocument();
      expect(screen.getByDisplayValue('ls_live_9999888877776666555544443333222211110000')).toBeInTheDocument();
    });

    // Test clipboard copy button with feedback state
    const copySpy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
    const copyBtn = screen.getByRole('button', { name: /Copy/i });
    fireEvent.click(copyBtn);

    await waitFor(() => {
      expect(copySpy).toHaveBeenCalledWith('ls_live_9999888877776666555544443333222211110000');
      expect(screen.getByText('Copied')).toBeInTheDocument();
    });

    // Dismiss one-time modal
    const doneBtn = screen.getByRole('button', { name: /Done/i });
    fireEvent.click(doneBtn);

    expect(screen.queryByText('Token Generated')).not.toBeInTheDocument();
  });

  it('renders active tokens in mobile card layout when viewport matches mobile', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue(mockTokens);
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    render(<ApiTokensTab />);

    await waitFor(() => {
      expect(screen.getByText('Active Tokens (2)')).toBeInTheDocument();
    });

    expect(screen.getByText('Backup Script')).toBeInTheDocument();
    expect(screen.getByText('Home Assistant')).toBeInTheDocument();
    const mobileContainer = screen.getByText('Backup Script').closest('.block.md\\:hidden');
    expect(mobileContainer).toBeInTheDocument();
  });

  it('revokes an API token with confirmation', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue(mockTokens);
    const revokeSpy = vi.spyOn(tokensApi, 'revokeApiToken').mockResolvedValue({ status: 'ok' });

    render(<ApiTokensTab />);

    await waitFor(() => {
      expect(screen.getByText('Active Tokens (2)')).toBeInTheDocument();
    });

    // Click revoke button for Backup Script
    const revokeBtns = screen.getAllByTitle('Revoke Token');
    fireEvent.click(revokeBtns[0]);

    expect(screen.getByText('Revoke Token')).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to revoke/i)).toBeInTheDocument();

    // Confirm revoke
    const confirmBtn = screen.getByRole('button', { name: /Yes, Revoke/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(revokeSpy).toHaveBeenCalledWith(1);
    });
  });

  it('correctly maps scope arrays to human-readable preset names', () => {
    expect(getPresetName(['*'])).toBe('Full Access');
    // Existing tokens created with the old four-scope MCP bundle keep a clear name
    expect(getPresetName(['logs:read', 'alerts:read', 'system:read', 'maintenance:write'])).toBe(
      'AI Assistant / MCP + Maintenance'
    );
    expect(getPresetName(['logs:read', 'alerts:read', 'system:read'])).toBe('Read Only');
    expect(getPresetName(['maintenance:write', 'maintenance:read'])).toBe('Maintenance Only');
    expect(getPresetName(['logs:write'])).toBe('Edge Shipper / Ingest');
    // Presets match by exact scope set; anything else is Custom
    expect(getPresetName(['system:read', 'logs:read', 'alerts:read'])).toBe('Read Only');
    expect(getPresetName(['logs:read', 'alerts:read', 'system:read', 'logs:write'])).toBe('Custom');
    expect(getPresetName(['maintenance:write', 'maintenance:read', 'logs:read'])).toBe('Custom');
    expect(getPresetName(['logs:*'])).toBe('Custom');
    expect(getPresetName(['system:write', 'system:read', 'maintenance:write'])).toBe('Home Assistant');
    expect(getPresetName(['system:read'])).toBe('Custom');
  });

  it('offers a read-only AI assistant preset by default and a separate preset with maintenance:write', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);
    const createSpy = vi.spyOn(tokensApi, 'createApiToken').mockResolvedValue({
      token: {
        id: 4,
        name: 'Deploy Agent',
        token_prefix: 'ls_live_1111...2222',
        scopes: ['logs:read', 'alerts:read', 'system:read', 'maintenance:write'],
        created_at: '2026-10-09T12:00:00Z',
        expires_at: null,
        last_used_at: null,
      },
      raw_token: 'ls_live_1111222233334444555566667777888899990000',
    });

    render(<ApiTokensTab />);
    await waitFor(() => {
      expect(screen.getByText('No API tokens generated yet')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Generate Token/i }));

    const readOnlyAssistant = screen.getByRole('radio', { name: /AI Assistant \/ MCP \(Read Only\)/ });
    const maintenanceAssistant = screen.getByRole('radio', { name: /AI Assistant \/ MCP \+ Maintenance/ });
    expect(readOnlyAssistant).toBeChecked();
    expect(maintenanceAssistant).not.toBeChecked();

    fireEvent.click(maintenanceAssistant);
    expect(maintenanceAssistant).toBeChecked();

    fireEvent.change(screen.getByPlaceholderText('e.g. Home Assistant or Claude Desktop'), {
      target: { value: 'Deploy Agent' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Create Token/i }));

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith({
        name: 'Deploy Agent',
        scopes: ['logs:read', 'alerts:read', 'system:read', 'maintenance:write'],
        expires_days: 90,
      });
    });
  });

  it('explains MCP scopes in the help text', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);

    render(<ApiTokensTab settings={{ enable_mcp: true }} />);
    await waitFor(() => {
      expect(screen.getByText(/Model Context Protocol \(MCP\)/i)).toBeInTheDocument();
    });

    const help = screen.getByText(/needs a Bearer token with at least/i);
    expect(help).toHaveTextContent('logs:read');
    expect(help).toHaveTextContent('maintenance:write');
    expect(help).toHaveTextContent(/untrusted/i);
  });

  it('renders MCP section disabled by default and shows disabled message', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);

    render(<ApiTokensTab settings={{ enable_mcp: false }} />);

    await waitFor(() => {
      expect(screen.getByText(/Model Context Protocol \(MCP\)/i)).toBeInTheDocument();
    });

    const mcpToggle = screen.getByLabelText(/Enable Model Context Protocol/i);
    expect(mcpToggle).not.toBeChecked();
    expect(screen.getByText('Disabled')).toBeInTheDocument();
    expect(screen.getByText(/MCP server is currently disabled/i)).toBeInTheDocument();
    expect(screen.queryByText(/Automated AI Redaction Notice/i)).not.toBeInTheDocument();
  });

  it('renders MCP section with SSE endpoint and collapsible client configuration options when enabled', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);

    render(<ApiTokensTab settings={{ enable_mcp: true }} />);

    await waitFor(() => {
      expect(screen.getByText(/Model Context Protocol \(MCP\)/i)).toBeInTheDocument();
    });

    const mcpToggle = screen.getByLabelText(/Enable Model Context Protocol/i);
    expect(mcpToggle).toBeChecked();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText(/Automated AI Redaction Notice/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue(/api\/v1\/mcp\/sse/)).toBeInTheDocument();

    // Verify all 3 collapsible options are present and collapsed by default
    const claudeProxyBtn = screen.getByRole('button', { name: /Remote via mcp-proxy/i });
    const cursorBtn = screen.getByRole('button', { name: /Remote SSE/i });
    const claudeDockerBtn = screen.getByRole('button', { name: /Local Docker Stdio/i });

    expect(claudeProxyBtn).toBeInTheDocument();
    expect(cursorBtn).toBeInTheDocument();
    expect(claudeDockerBtn).toBeInTheDocument();

    expect(claudeProxyBtn).toHaveAttribute('aria-expanded', 'false');
    expect(cursorBtn).toHaveAttribute('aria-expanded', 'false');
    expect(claudeDockerBtn).toHaveAttribute('aria-expanded', 'false');

    // Snippet content is not visible while collapsed
    expect(screen.queryByText((content) => content.includes('"command": "mcp-proxy"'))).not.toBeInTheDocument();
    expect(screen.queryByText((content) => content.includes('"python", "-m", "app.cli", "mcp"'))).not.toBeInTheDocument();

    // Expand the Claude Proxy accordion
    fireEvent.click(claudeProxyBtn);
    expect(claudeProxyBtn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText((content) => content.includes('"command": "mcp-proxy"'))).toBeInTheDocument();

    // Expand the Cursor SSE accordion
    fireEvent.click(cursorBtn);
    expect(cursorBtn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText((content) => content.includes('"Authorization": "Bearer ls_live_YOUR_TOKEN_HERE"'))).toBeInTheDocument();

    // Expand the Claude Docker accordion
    fireEvent.click(claudeDockerBtn);
    expect(claudeDockerBtn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText((content) => content.includes('"python", "-m", "app.cli", "mcp"'))).toBeInTheDocument();

    // Collapse Claude Proxy accordion again
    fireEvent.click(claudeProxyBtn);
    expect(claudeProxyBtn).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText((content) => content.includes('"command": "mcp-proxy"'))).not.toBeInTheDocument();
  });

  it('toggles MCP setting on and calls updateSettings and onSettingsSaved', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);
    const updateSpy = vi.spyOn(settingsApi, 'updateSettings').mockResolvedValue({ status: 'ok' });
    const onSaved = vi.fn();

    render(
      <ApiTokensTab
        settings={{ enable_mcp: false }}
        onSettingsSaved={onSaved}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Model Context Protocol \(MCP\)/i)).toBeInTheDocument();
    });

    const mcpToggle = screen.getByLabelText(/Enable Model Context Protocol/i);
    expect(mcpToggle).not.toBeChecked();

    fireEvent.click(mcpToggle);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ enable_mcp: true });
      expect(onSaved).toHaveBeenCalled();
    });
  });

  it('opens generate token modal when clicking Generate API Token in MCP helper', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);

    render(<ApiTokensTab settings={{ enable_mcp: true }} />);

    await waitFor(() => {
      expect(screen.getByText(/Model Context Protocol \(MCP\)/i)).toBeInTheDocument();
    });

    const mcpGenBtn = screen.getByRole('button', { name: /Generate API Token/i });
    fireEvent.click(mcpGenBtn);

    expect(screen.getByRole('heading', { name: /Generate API Token/i })).toBeInTheDocument();
    expect(screen.getAllByText(/AI Assistant \/ MCP/i).length).toBeGreaterThanOrEqual(2);
  });

  it('uses configured app_url for SSE endpoint and client snippet when present', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);

    render(
      <ApiTokensTab
        settings={{
          enable_mcp: true,
          app_url: 'https://logs.example.com/',
        }}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Model Context Protocol \(MCP\)/i)).toBeInTheDocument();
    });

    expect(screen.getByDisplayValue('https://logs.example.com/api/v1/mcp/sse')).toBeInTheDocument();

    // Expand Cursor SSE accordion to verify snippet uses app_url
    const cursorBtn = screen.getByRole('button', { name: /Remote SSE/i });
    fireEvent.click(cursorBtn);
    expect(screen.getByText((content) => content.includes('https://logs.example.com/api/v1/mcp/sse'))).toBeInTheDocument();
  });

  it('shows a token create error inside the generate modal', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue(mockTokens);
    vi.spyOn(tokensApi, 'createApiToken').mockRejectedValue(new Error('Token name already exists'));

    render(<ApiTokensTab />);
    await waitFor(() => {
      expect(screen.getByText('Active Tokens (2)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Generate Token/i }));
    fireEvent.change(screen.getByPlaceholderText('e.g. Home Assistant or Claude Desktop'), {
      target: { value: 'Dup' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Create Token/i }));

    const alert = await screen.findByText('Token name already exists');
    expect(alert.closest('[data-testid="generate-token-modal"]')).not.toBeNull();
    expect(screen.getByText('Generate API Token')).toBeInTheDocument();
  });

  it('shows a token revoke error inside the revoke confirmation', async () => {
    vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue(mockTokens);
    vi.spyOn(tokensApi, 'revokeApiToken').mockRejectedValue(new Error('Revoke failed upstream'));

    render(<ApiTokensTab />);
    await waitFor(() => {
      expect(screen.getByText('Active Tokens (2)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByTitle('Revoke Token')[0]);
    fireEvent.click(screen.getByRole('button', { name: /Yes, Revoke/i }));

    const alert = await screen.findByText('Revoke failed upstream');
    expect(alert.closest('[data-testid="revoke-token-modal"]')).not.toBeNull();
    expect(screen.getByText(/Are you sure you want to revoke/i)).toBeInTheDocument();
  });

  describe('custom scope picker in the generate modal', () => {
    const createdToken = {
      token: {
        id: 9,
        name: 'Picker',
        token_prefix: 'ls_live_9999...9999',
        scopes: ['logs:*'],
        created_at: '2026-10-09T12:00:00Z',
        expires_at: null,
        last_used_at: null,
      },
      raw_token: 'ls_live_9999999999999999999999999999999999999999',
    };

    // Opens the generate modal on Custom with nothing ticked
    const openEmptyCustomPicker = async () => {
      vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);
      const createSpy = vi.spyOn(tokensApi, 'createApiToken').mockResolvedValue(createdToken);
      render(<ApiTokensTab />);
      await waitFor(() => {
        expect(screen.getByText('No API tokens generated yet')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByRole('button', { name: /Generate Token/i }));
      fireEvent.change(screen.getByPlaceholderText('e.g. Home Assistant or Claude Desktop'), {
        target: { value: 'Picker' },
      });
      fireEvent.click(screen.getByRole('radio', { name: /^Custom/ }));
      const root = screen.getByRole('checkbox', { name: /Full Access/ });
      fireEvent.click(root);
      fireEvent.click(root);
      expect(screen.getByText('Choose at least one scope')).toBeInTheDocument();
      return createSpy;
    };

    const submit = () => fireEvent.click(screen.getByRole('button', { name: /Create Token/i }));

    it('starts Custom as a copy of the selected preset', async () => {
      vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([]);
      render(<ApiTokensTab />);
      await waitFor(() => {
        expect(screen.getByText('No API tokens generated yet')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByRole('button', { name: /Generate Token/i }));
      fireEvent.click(screen.getByRole('radio', { name: /AI Assistant \/ MCP \+ Maintenance/ }));
      fireEvent.click(screen.getByRole('radio', { name: /^Custom/ }));

      expect(screen.getByRole('group', { name: 'Custom scopes' })).toBeInTheDocument();
      expect(screen.getByRole('checkbox', { name: 'maintenance:write' })).toBeChecked();
      expect(screen.getByRole('checkbox', { name: 'maintenance:read' })).not.toBeChecked();
      expect(screen.getByTestId('create-token-saved-scopes')).toHaveTextContent(
        'logs:read, alerts:read, system:read, maintenance:write'
      );
    });

    it('disables submit while nothing is ticked', async () => {
      await openEmptyCustomPicker();
      expect(screen.getByRole('button', { name: /Create Token/i })).toBeDisabled();
    });

    it('ticking a category ticks its actions and saves the wildcard', async () => {
      const createSpy = await openEmptyCustomPicker();
      fireEvent.click(screen.getByRole('checkbox', { name: /logs:\*/ }));
      expect(screen.getByRole('checkbox', { name: 'logs:read' })).toBeChecked();
      expect(screen.getByRole('checkbox', { name: 'logs:write' })).toBeChecked();
      expect(screen.getByTestId('create-token-saved-scopes')).toHaveTextContent(/^logs:\*$/);

      submit();
      await waitFor(() => {
        expect(createSpy).toHaveBeenLastCalledWith({ name: 'Picker', scopes: ['logs:*'], expires_days: 90 });
      });
    });

    it('unticking an action of a ticked category keeps its siblings', async () => {
      const createSpy = await openEmptyCustomPicker();
      fireEvent.click(screen.getByRole('checkbox', { name: /logs:\*/ }));
      fireEvent.click(screen.getByRole('checkbox', { name: 'logs:write' }));
      expect(screen.getByRole('checkbox', { name: /logs:\*/ })).not.toBeChecked();
      expect(screen.getByRole('checkbox', { name: 'logs:read' })).toBeChecked();

      submit();
      await waitFor(() => {
        expect(createSpy).toHaveBeenLastCalledWith({ name: 'Picker', scopes: ['logs:read'], expires_days: 90 });
      });
    });

    it('unticking an action under Full Access keeps every other category as a wildcard', async () => {
      const createSpy = await openEmptyCustomPicker();
      fireEvent.click(screen.getByRole('checkbox', { name: /Full Access/ }));
      fireEvent.click(screen.getByRole('checkbox', { name: 'logs:write' }));
      expect(screen.getByTestId('create-token-saved-scopes')).toHaveTextContent(
        'logs:read, alerts:*, system:*, maintenance:*'
      );

      submit();
      await waitFor(() => {
        expect(createSpy).toHaveBeenLastCalledWith({
          name: 'Picker',
          scopes: ['logs:read', 'alerts:*', 'system:*', 'maintenance:*'],
          expires_days: 90,
        });
      });
    });

    it('ticking every action one by one leaves the category indeterminate', async () => {
      const createSpy = await openEmptyCustomPicker();
      fireEvent.click(screen.getByRole('checkbox', { name: 'logs:read' }));
      fireEvent.click(screen.getByRole('checkbox', { name: 'logs:write' }));
      const category = screen.getByRole('checkbox', { name: /logs:\*/ }) as HTMLInputElement;
      expect(category).not.toBeChecked();
      expect(category.indeterminate).toBe(true);
      expect((screen.getByRole('checkbox', { name: /Full Access/ }) as HTMLInputElement).indeterminate).toBe(true);

      submit();
      await waitFor(() => {
        expect(createSpy).toHaveBeenLastCalledWith({
          name: 'Picker',
          scopes: ['logs:read', 'logs:write'],
          expires_days: 90,
        });
      });
    });
  });

  describe('edit token modal', () => {
    const readOnlyToken: ApiToken = {
      id: 3,
      name: 'Claude Read-Only',
      token_prefix: 'ls_live_3333...3333',
      scopes: ['logs:read', 'alerts:read', 'system:read'],
      created_at: '2026-10-09T10:00:00Z',
      expires_at: null,
      last_used_at: null,
      created_by: 'admin',
    };
    const customToken: ApiToken = {
      ...readOnlyToken,
      id: 4,
      name: 'Hand Built',
      token_prefix: 'ls_live_4444...4444',
      scopes: ['logs:read', 'alerts:read', 'system:read', 'logs:write'],
    };
    const logsActionsToken: ApiToken = {
      ...readOnlyToken,
      id: 5,
      name: 'Logs Both',
      token_prefix: 'ls_live_5555...5555',
      scopes: ['logs:read', 'logs:write'],
    };

    const openEdit = async (token: ApiToken) => {
      const fetchSpy = vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([token]);
      const updateSpy = vi.spyOn(tokensApi, 'updateApiToken').mockResolvedValue(token);
      render(<ApiTokensTab />);
      await waitFor(() => {
        expect(screen.getByText('Active Tokens (1)')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByTitle('Edit Token'));
      const modal = within(screen.getByTestId('edit-token-modal'));
      return { fetchSpy, updateSpy, modal };
    };

    it('preselects the matching preset and the current name, and keeps Save disabled until something changes', async () => {
      const { modal } = await openEdit(readOnlyToken);
      expect(modal.getByRole('heading', { name: 'Edit API token' })).toBeInTheDocument();
      expect(modal.getByLabelText('Name')).toHaveValue('Claude Read-Only');
      // Read Only and AI Assistant / MCP (Read Only) share these scopes: the first one is selected
      expect(modal.getByRole('radio', { name: /^Read Only/ })).toBeChecked();
      expect(modal.getByRole('radio', { name: /AI Assistant \/ MCP \(Read Only\)/ })).not.toBeChecked();
      expect(modal.getByText(/stays the same, so clients need no update/)).toBeInTheDocument();

      const save = modal.getByRole('button', { name: 'Save' });
      expect(save).toBeDisabled();
      // The other preset with the same scopes is not a change
      fireEvent.click(modal.getByRole('radio', { name: /AI Assistant \/ MCP \(Read Only\)/ }));
      expect(save).toBeDisabled();

      fireEvent.change(modal.getByLabelText('Name'), { target: { value: 'Claude' } });
      expect(save).toBeEnabled();
      fireEvent.change(modal.getByLabelText('Name'), { target: { value: '   ' } });
      expect(save).toBeDisabled();
      fireEvent.change(modal.getByLabelText('Name'), { target: { value: ' Claude Read-Only ' } });
      expect(save).toBeDisabled();
    });

    it('a rename sends only the name, saves and refreshes the list', async () => {
      const { modal, updateSpy, fetchSpy } = await openEdit(readOnlyToken);
      fireEvent.change(modal.getByLabelText('Name'), { target: { value: ' Claude Maintenance ' } });
      expect(screen.queryByTestId('scope-gain-warning')).not.toBeInTheDocument();
      fireEvent.click(modal.getByRole('button', { name: 'Save' }));

      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(3, { name: 'Claude Maintenance' });
        expect(fetchSpy).toHaveBeenCalledTimes(2);
      });
      expect(screen.queryByTestId('edit-token-modal')).not.toBeInTheDocument();
    });

    it('a scope change sends only the scopes and warns about added access', async () => {
      const { modal, updateSpy } = await openEdit(readOnlyToken);
      fireEvent.click(modal.getByRole('radio', { name: /AI Assistant \/ MCP \+ Maintenance/ }));

      const warning = screen.getByTestId('scope-gain-warning');
      expect(warning).toHaveTextContent('This token gains: maintenance:write.');
      expect(warning).toHaveTextContent(/straight away/);
      expect(warning).toHaveTextContent(/untrusted and can try to steer an assistant/);

      fireEvent.click(modal.getByRole('button', { name: 'Save' }));
      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(3, {
          scopes: ['logs:read', 'alerts:read', 'system:read', 'maintenance:write'],
        });
      });
    });

    it('moves a sensors-only Home Assistant token to the Home Assistant preset without the assistant warning', async () => {
      const sensorsOnly: ApiToken = { ...readOnlyToken, id: 6, name: 'Home Assistant', scopes: ['system:read'] };
      const { modal, updateSpy } = await openEdit(sensorsOnly);
      expect(modal.getByRole('radio', { name: /^Custom/ })).toBeChecked();
      fireEvent.click(modal.getByRole('radio', { name: /^Home Assistant/ }));

      const warning = screen.getByTestId('scope-gain-warning');
      expect(warning).toHaveTextContent('This token gains: system:write, maintenance:write.');
      expect(warning).not.toHaveTextContent(/steer an assistant/);
      // The name still fits, but the hint is shown for any scope change
      expect(modal.getByText(/The name still says 'Home Assistant'/)).toBeInTheDocument();

      fireEvent.click(modal.getByRole('button', { name: 'Save' }));
      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(6, { scopes: ['system:read', 'maintenance:write', 'system:write'] });
      });
    });

    it('warns prominently about gaining full access', async () => {
      const { modal } = await openEdit(readOnlyToken);
      fireEvent.click(modal.getByRole('radio', { name: /^Full Access/ }));
      const warning = screen.getByTestId('scope-gain-warning');
      expect(warning).toHaveTextContent('This token gains: *.');
      expect(warning).toHaveTextContent(/any action added in later versions/);
    });

    it('does not warn when access is only removed', async () => {
      const { modal, updateSpy } = await openEdit(customToken);
      expect(modal.getByRole('radio', { name: /^Custom/ })).toBeChecked();
      fireEvent.click(modal.getByRole('checkbox', { name: 'logs:write' }));
      expect(screen.queryByTestId('scope-gain-warning')).not.toBeInTheDocument();

      fireEvent.click(modal.getByRole('button', { name: 'Save' }));
      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(4, { scopes: ['logs:read', 'alerts:read', 'system:read'] });
      });
    });

    it('warns when a category wildcard replaces all of its actions', async () => {
      const { modal } = await openEdit(logsActionsToken);
      expect(modal.getByRole('radio', { name: /^Custom/ })).toBeChecked();
      expect(modal.getByRole('button', { name: 'Save' })).toBeDisabled();
      fireEvent.click(modal.getByRole('checkbox', { name: /logs:\*/ }));
      expect(screen.getByTestId('scope-gain-warning')).toHaveTextContent('This token gains: logs:*.');
      expect(modal.getByRole('button', { name: 'Save' })).toBeEnabled();
    });

    it('lists a token that matches no preset as Custom without its scopes', async () => {
      vi.spyOn(tokensApi, 'fetchApiTokens').mockResolvedValue([customToken]);
      render(<ApiTokensTab />);
      await waitFor(() => {
        expect(screen.getByText('Active Tokens (1)')).toBeInTheDocument();
      });
      expect(screen.getByText('Custom')).toBeInTheDocument();
      expect(screen.queryByText('logs:write')).not.toBeInTheDocument();
    });

    it('links the scope guide from the scope chooser', async () => {
      const { modal } = await openEdit(readOnlyToken);
      expect(modal.getByRole('link', { name: /Scope Guide/ })).toHaveAttribute(
        'href',
        'https://github.com/logshed/logshed/blob/main/docs/API_SCOPES.md'
      );
    });

    it('opens a token that matches no preset on Custom with its scopes ticked', async () => {
      const { modal } = await openEdit(customToken);
      expect(screen.getByText('Custom', { selector: 'span' })).toBeInTheDocument();
      expect(modal.getByRole('radio', { name: /^Custom/ })).toBeChecked();
      for (const scope of customToken.scopes) {
        expect(modal.getByRole('checkbox', { name: scope })).toBeChecked();
      }
      expect(modal.getByRole('checkbox', { name: 'system:write' })).not.toBeChecked();
      expect(modal.getByRole('checkbox', { name: /logs:\*/ })).not.toBeChecked();
      expect(modal.getByTestId('edit-token-saved-scopes')).toHaveTextContent(
        'logs:read, logs:write, alerts:read, system:read'
      );
    });

    it('hints at a rename after a scope change until the name is edited', async () => {
      const { modal } = await openEdit(readOnlyToken);
      expect(modal.queryByText(/The name still says/)).not.toBeInTheDocument();

      fireEvent.click(modal.getByRole('radio', { name: /^Maintenance Only/ }));
      expect(modal.getByText("The name still says 'Claude Read-Only'. Rename it if it no longer fits.")).toBeInTheDocument();

      fireEvent.change(modal.getByLabelText('Name'), { target: { value: 'Backup Hook' } });
      expect(modal.queryByText(/The name still says/)).not.toBeInTheDocument();
    });

    it('starts Custom from the token scopes and sends both fields when both change', async () => {
      const { modal, updateSpy } = await openEdit(readOnlyToken);
      fireEvent.click(modal.getByRole('radio', { name: /^Full Access/ }));
      fireEvent.click(modal.getByRole('radio', { name: /^Custom/ }));
      expect(modal.getByTestId('edit-token-saved-scopes')).toHaveTextContent('logs:read, alerts:read, system:read');

      fireEvent.click(modal.getByRole('checkbox', { name: /maintenance:\*/ }));
      fireEvent.change(modal.getByLabelText('Name'), { target: { value: 'Claude Maintenance' } });
      fireEvent.click(modal.getByRole('button', { name: 'Save' }));
      await waitFor(() => {
        expect(updateSpy).toHaveBeenCalledWith(3, {
          name: 'Claude Maintenance',
          scopes: ['logs:read', 'alerts:read', 'system:read', 'maintenance:*'],
        });
      });
    });

    it('shows a save error inside the edit modal', async () => {
      const { modal, updateSpy } = await openEdit(readOnlyToken);
      updateSpy.mockRejectedValue(new Error('API token 3 not found.'));
      fireEvent.change(modal.getByLabelText('Name'), { target: { value: 'Gone' } });
      fireEvent.click(modal.getByRole('button', { name: 'Save' }));

      const alert = await screen.findByText('API token 3 not found.');
      expect(alert.closest('[data-testid="edit-token-modal"]')).not.toBeNull();
    });
  });
});
