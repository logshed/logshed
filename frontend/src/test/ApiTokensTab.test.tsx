import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
    expect(getPresetName(['custom:read'])).toBe('custom:read');
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
});

