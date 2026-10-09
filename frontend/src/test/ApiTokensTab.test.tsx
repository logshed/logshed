import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ApiTokensTab, getPresetName } from '../components/settings/ApiTokensTab.tsx';
import * as tokensApi from '../api/tokens.ts';
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
        scopes: ['logs:read', 'alerts:read', 'system:read', 'maintenance:write'],
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
        scopes: ['logs:read', 'alerts:read', 'system:read', 'maintenance:write'],
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
    expect(getPresetName(['logs:read', 'alerts:read', 'system:read', 'maintenance:write'])).toBe('AI Assistant / MCP');
    expect(getPresetName(['logs:read', 'alerts:read', 'system:read'])).toBe('Read Only');
    expect(getPresetName(['maintenance:write', 'maintenance:read'])).toBe('Maintenance Only');
    expect(getPresetName(['custom:read'])).toBe('custom:read');
  });
});
