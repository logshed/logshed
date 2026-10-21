import React, { useEffect, useState, useRef } from 'react';
import {
  Key,
  Plus,
  Trash2,
  Copy,
  Check,
  AlertCircle,
  AlertTriangle,
  X,
  Bot,
  Shield,
  ChevronDown,
  ChevronRight,
  Edit2,
} from 'lucide-react';
import { ApiToken, ApiTokenCreateRequest, ApiTokenUpdateRequest, SystemSettings } from '../../types.ts';
import { fetchApiTokens, createApiToken, revokeApiToken, updateApiToken } from '../../api/tokens.ts';
import { updateSettings } from '../../api/settings.ts';
import { formatDate } from '../../utils/formatters.ts';

import { useClipboard, useMediaQuery } from '../../utils/hooks.ts';
import { TokenScopeChooser } from './TokenScopeChooser.tsx';
import {
  CUSTOM_PRESET_ID,
  FULL_ACCESS_SCOPE,
  SCOPE_PRESETS,
  addedAccess,
  collapseScopes,
  expandScopes,
  findMatchingPreset,
  sameScopeSet,
  scopeGrants,
} from './tokenScopes.ts';

const DEFAULT_PRESET_ID = 'ai_mcp';

/** The scopes a modal would save: the chosen preset's list, or the collapsed custom selection. */
const chosenScopes = (presetId: string, selection: ReadonlySet<string>): string[] =>
  presetId === CUSTOM_PRESET_ID
    ? collapseScopes(selection)
    : (SCOPE_PRESETS.find((p) => p.id === presetId)?.scopes ?? []);

// Exact match against SCOPE_PRESETS: the Read Only and AI Assistant / MCP (Read Only) presets grant the
// same scopes and show as "Read Only"; any other combination is "Custom".
export const getPresetName = (scopes: string[]): string => findMatchingPreset(scopes)?.name ?? 'Custom';

export interface ApiTokensTabProps {
  settings?: Partial<SystemSettings> | null;
  onSettingsSaved?: () => Promise<void> | void;
}

export const ApiTokensTab: React.FC<ApiTokensTabProps> = ({
  settings,
  onSettingsSaved,
}) => {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Errors raised by actions inside a modal are shown in the modal, not under its backdrop
  const [modalError, setModalError] = useState<string | null>(null);

  // Modals
  const [isGenerateModalOpen, setIsGenerateModalOpen] = useState<boolean>(false);
  const [newTokenName, setNewTokenName] = useState<string>('');
  const [selectedPresetId, setSelectedPresetId] = useState<string>(DEFAULT_PRESET_ID);
  const [newTokenSelection, setNewTokenSelection] = useState<Set<string>>(new Set());
  const [expiresDays, setExpiresDays] = useState<number | null>(90);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  // Edit modal (name and scopes)
  const [tokenToEdit, setTokenToEdit] = useState<ApiToken | null>(null);
  const [editName, setEditName] = useState<string>('');
  const [editPresetId, setEditPresetId] = useState<string>(DEFAULT_PRESET_ID);
  const [editSelection, setEditSelection] = useState<Set<string>>(new Set());
  const [isSavingEdit, setIsSavingEdit] = useState<boolean>(false);

  // One-time created token modal
  const [createdRawToken, setCreatedRawToken] = useState<string | null>(null);
  const { copied: hasCopied, copy: copyToken } = useClipboard();
  const tokenInputRef = useRef<HTMLInputElement>(null);

  // Revoke confirmation modal
  const [tokenToRevoke, setTokenToRevoke] = useState<ApiToken | null>(null);
  const [isRevoking, setIsRevoking] = useState<boolean>(false);

  // MCP settings state & clipboards
  const [enableMcp, setEnableMcp] = useState<boolean>(settings?.enable_mcp ?? false);
  const [isUpdatingMcp, setIsUpdatingMcp] = useState<boolean>(false);
  const [mcpErrorMsg, setMcpErrorMsg] = useState<string | null>(null);

  const { copied: hasCopiedSse, copy: copySse } = useClipboard();
  const { copied: hasCopiedClaudeProxy, copy: copyClaudeProxy } = useClipboard();
  const { copied: hasCopiedCursor, copy: copyCursor } = useClipboard();
  const { copied: hasCopiedClaudeDocker, copy: copyClaudeDocker } = useClipboard();

  // Collapsible configuration snippets state (all collapsed by default)
  const [expandedSnippets, setExpandedSnippets] = useState<Record<string, boolean>>({});

  const toggleSnippet = (key: string) => {
    setExpandedSnippets((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

  useEffect(() => {
    if (settings?.enable_mcp !== undefined) {
      setEnableMcp(settings.enable_mcp);
    }
  }, [settings?.enable_mcp]);

  const handleToggleMcp = async (checked: boolean) => {
    setIsUpdatingMcp(true);
    setMcpErrorMsg(null);
    setEnableMcp(checked);
    try {
      await updateSettings({ enable_mcp: checked });
      await onSettingsSaved?.();
    } catch (err: any) {
      setEnableMcp(!checked);
      setMcpErrorMsg(err.message || 'Failed to update MCP setting.');
    } finally {
      setIsUpdatingMcp(false);
    }
  };

  const configuredAppUrl = settings?.app_url?.trim().replace(/\/+$/, '');
  const origin = configuredAppUrl || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8080');
  const sseUrl = `${origin}/api/v1/mcp/sse`;

  const claudeProxyConfigSnippet = JSON.stringify(
    {
      mcpServers: {
        logshed: {
          command: 'mcp-proxy',
          args: ['--transport=sse', sseUrl],
          env: {
            API_ACCESS_TOKEN: 'ls_live_YOUR_TOKEN_HERE',
          },
        },
      },
    },
    null,
    2
  );

  const cursorConfigSnippet = JSON.stringify(
    {
      mcpServers: {
        logshed: {
          url: sseUrl,
          headers: {
            Authorization: 'Bearer ls_live_YOUR_TOKEN_HERE',
          },
        },
      },
    },
    null,
    2
  );

  const claudeDockerConfigSnippet = JSON.stringify(
    {
      mcpServers: {
        logshed: {
          command: 'docker',
          args: ['exec', '-i', 'logshed', 'python', '-m', 'app.cli', 'mcp'],
        },
      },
    },
    null,
    2
  );

  const loadTokens = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const data = await fetchApiTokens();
      setTokens(data);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load API tokens.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadTokens();
  }, []);

  const handleOpenGenerateModal = () => {
    setModalError(null);
    setNewTokenName('');
    setSelectedPresetId(DEFAULT_PRESET_ID);
    setNewTokenSelection(new Set());
    setExpiresDays(90);
    setIsGenerateModalOpen(true);
  };

  // Custom starts as a copy of the preset that was selected
  const handleGeneratePresetSelect = (id: string) => {
    if (id === CUSTOM_PRESET_ID && selectedPresetId !== CUSTOM_PRESET_ID) {
      setNewTokenSelection(expandScopes(chosenScopes(selectedPresetId, newTokenSelection)));
    }
    setSelectedPresetId(id);
  };

  const newTokenScopes = chosenScopes(selectedPresetId, newTokenSelection);

  const handleGenerateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTokenName.trim() || newTokenScopes.length === 0) return;

    setIsSubmitting(true);
    setErrorMsg(null);
    setModalError(null);

    try {
      const payload: ApiTokenCreateRequest = {
        name: newTokenName.trim(),
        scopes: newTokenScopes,
        expires_days: expiresDays,
      };
      const resp = await createApiToken(payload);
      setIsGenerateModalOpen(false);
      setCreatedRawToken(resp.raw_token);
      await loadTokens();
    } catch (err: any) {
      setModalError(err.message || 'Failed to generate token.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOpenEditModal = (token: ApiToken) => {
    setModalError(null);
    setTokenToEdit(token);
    setEditName(token.name);
    setEditPresetId(findMatchingPreset(token.scopes)?.id ?? CUSTOM_PRESET_ID);
    setEditSelection(expandScopes(token.scopes));
  };

  // Custom starts as a copy of the token's current scopes
  const handleEditPresetSelect = (id: string) => {
    if (id === CUSTOM_PRESET_ID && editPresetId !== CUSTOM_PRESET_ID && tokenToEdit) {
      setEditSelection(expandScopes(tokenToEdit.scopes));
    }
    setEditPresetId(id);
  };

  const editScopes = chosenScopes(editPresetId, editSelection);
  const editTrimmedName = editName.trim();
  const editNameChanged = !!tokenToEdit && editTrimmedName !== tokenToEdit.name;
  const editScopesChanged =
    !!tokenToEdit && editScopes.length > 0 && !sameScopeSet(editScopes, collapseScopes(tokenToEdit.scopes));
  const editGains = tokenToEdit && editScopesChanged ? addedAccess(tokenToEdit.scopes, editScopes) : [];
  const editGainsFullAccess = editGains.includes(FULL_ACCESS_SCOPE);
  // A token that can connect over MCP and gains maintenance:write lets an assistant start and end maintenance
  const editGainsAssistantMaintenance =
    !!tokenToEdit &&
    editScopesChanged &&
    scopeGrants(editScopes, 'logs:read') &&
    scopeGrants(editScopes, 'maintenance:write') &&
    !scopeGrants(tokenToEdit.scopes, 'maintenance:write');
  const canSaveEdit =
    !isSavingEdit && editTrimmedName !== '' && editScopes.length > 0 && (editNameChanged || editScopesChanged);

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenToEdit || !canSaveEdit) return;

    setIsSavingEdit(true);
    setModalError(null);
    try {
      const payload: ApiTokenUpdateRequest = {};
      if (editNameChanged) payload.name = editTrimmedName;
      if (editScopesChanged) payload.scopes = editScopes;
      await updateApiToken(tokenToEdit.id, payload);
      setTokenToEdit(null);
      await loadTokens();
    } catch (err: any) {
      setModalError(err.message || 'Failed to update token.');
    } finally {
      setIsSavingEdit(false);
    }
  };

  const handleCopyRawToken = async () => {
    if (!createdRawToken) return;
    if (tokenInputRef.current) {
      tokenInputRef.current.focus();
      tokenInputRef.current.select();
      tokenInputRef.current.setSelectionRange(0, 99999);
    }
    await copyToken(createdRawToken);
  };

  const handleConfirmRevoke = async () => {
    if (!tokenToRevoke) return;
    setIsRevoking(true);
    setModalError(null);
    try {
      await revokeApiToken(tokenToRevoke.id);
      setTokenToRevoke(null);
      await loadTokens();
    } catch (err: any) {
      setModalError(err.message || 'Failed to revoke token.');
    } finally {
      setIsRevoking(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header Card */}
      <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Key className="w-4 h-4 text-accent-500" />
              <span>External API Access</span>
            </h3>
            <p className="text-xs text-slate-400 mt-1 max-w-2xl leading-relaxed">
              Generate secure Bearer tokens for external automation, Home Assistant integrations,
              and AI assistants. All API requests authenticate via HTTP header{' '}
              <code className="text-accent-400 font-mono text-[11px] bg-dark-950 px-1.5 py-0.5 rounded border border-dark-800">
                Authorization: Bearer ls_live_...
              </code>
            </p>
          </div>
          <button
            type="button"
            onClick={handleOpenGenerateModal}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold bg-accent-600 hover:bg-accent-500 text-white rounded-lg transition shadow-xs cursor-pointer shrink-0 self-start sm:self-auto"
          >
            <Plus className="w-4 h-4" />
            <span>Generate Token</span>
          </button>
        </div>

        {errorMsg && (
          <div className="mt-4 p-3 rounded-lg border bg-red-950/60 border-red-800 text-red-300 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}
      </section>

      {/* Token List Table */}
      <section className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-xs">
        <div className="px-5 py-3.5 border-b border-dark-700 flex items-center justify-between">
          <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
            Active Tokens ({tokens.length})
          </h3>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-xs text-slate-400 font-mono">
            Loading tokens...
          </div>
        ) : tokens.length === 0 ? (
          <div className="p-8 text-center space-y-2">
            <Key className="w-8 h-8 text-slate-600 mx-auto" />
            <p className="text-xs font-medium text-slate-300">No API tokens generated yet</p>
            <p className="text-xs text-slate-500 max-w-sm mx-auto">
              Create a token to allow Home Assistant, backup runners, or AI agents to access LogShed.
            </p>
          </div>
        ) : isMobile ? (
          /* Mobile Card View */
            <div className="block md:hidden divide-y divide-dark-800">
              {tokens.map((token) => {
                const presetName = getPresetName(token.scopes);
                return (
                  <div key={`mobile-${token.id}`} className="p-4 space-y-2.5">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-semibold text-slate-100">{token.name}</span>
                          <span className="font-mono text-[11px] text-slate-300 bg-dark-950 px-1.5 py-0.5 rounded border border-dark-750">
                            {token.token_prefix}
                          </span>
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium whitespace-nowrap bg-dark-800 text-accent-300 border border-dark-700">
                            {presetName}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => handleOpenEditModal(token)}
                          className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-slate-300 hover:text-slate-100 hover:bg-dark-800 border border-dark-700 rounded-lg transition cursor-pointer"
                          title="Edit Token"
                        >
                          <Edit2 className="w-3.5 h-3.5" />
                          <span>Edit</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => { setModalError(null); setTokenToRevoke(token); }}
                          className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-red-400 hover:text-red-300 hover:bg-red-950/40 border border-dark-700 hover:border-red-900 rounded-lg transition cursor-pointer"
                          title="Revoke Token"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          <span>Revoke</span>
                        </button>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
                      <span>
                        Created: <span className="text-slate-300">{formatDate(token.created_at)}</span>
                      </span>
                      <span>
                        Expires:{' '}
                        <span className="text-slate-300">
                          {token.expires_at ? formatDate(token.expires_at) : 'Never'}
                        </span>
                      </span>
                      <span>
                        Last Used:{' '}
                        <span className="text-slate-300">
                          {token.last_used_at ? formatDate(token.last_used_at) : 'Never'}
                        </span>
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
        ) : (
          /* Desktop Table View */
          <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-dark-700/60 bg-dark-950/40 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    <th className="py-2.5 px-4">Name</th>
                    <th className="py-2.5 px-4 font-mono">Identifier</th>
                    <th className="py-2.5 px-4">Scopes</th>
                    <th className="py-2.5 px-4">Created</th>
                    <th className="py-2.5 px-4">Expires</th>
                    <th className="py-2.5 px-4">Last Used</th>
                    <th className="py-2.5 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-dark-800 text-slate-200">
                  {tokens.map((token) => {
                    const presetName = getPresetName(token.scopes);
                    return (
                      <tr key={`desktop-${token.id}`} className="hover:bg-dark-800/40 transition">
                        <td className="py-3 px-4 font-medium text-slate-100">
                          {token.name}
                        </td>
                        <td className="py-3 px-4 font-mono text-[11px] text-slate-300">
                          {token.token_prefix}
                        </td>
                        <td className="py-3 px-4">
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium whitespace-nowrap bg-dark-800 text-accent-300 border border-dark-700">
                            {presetName}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-slate-400">
                          {formatDate(token.created_at)}
                        </td>
                        <td className="py-3 px-4 text-slate-400">
                          {token.expires_at ? formatDate(token.expires_at) : (
                            <span className="text-slate-500">Never</span>
                          )}
                        </td>
                        <td className="py-3 px-4 text-slate-400">
                          {token.last_used_at ? formatDate(token.last_used_at) : (
                            <span className="text-slate-500">Never</span>
                          )}
                        </td>
                        <td className="py-3 px-4 text-right whitespace-nowrap">
                          <button
                            type="button"
                            onClick={() => handleOpenEditModal(token)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-slate-300 hover:text-slate-100 hover:bg-dark-800 border border-transparent hover:border-dark-700 rounded transition cursor-pointer"
                            title="Edit Token"
                          >
                            <Edit2 className="w-3.5 h-3.5" />
                            <span>Edit</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => { setModalError(null); setTokenToRevoke(token); }}
                            className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-red-400 hover:text-red-300 hover:bg-red-950/40 border border-transparent hover:border-red-900 rounded transition cursor-pointer"
                            title="Revoke Token"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                            <span>Revoke</span>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
        )}
      </section>

      {/* Model Context Protocol (MCP) Section */}
      <section className="bg-dark-900 border border-dark-700 rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-dark-800">
          <div>
            <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
              <Bot className="w-4 h-4 text-accent-500" />
              <span>Model Context Protocol (MCP)</span>
              {enableMcp ? (
                <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-950/80 text-emerald-400 border border-emerald-800/80 normal-case tracking-normal">
                  Active
                </span>
              ) : (
                <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-dark-800 text-slate-400 border border-dark-700 normal-case tracking-normal">
                  Disabled
                </span>
              )}
            </h3>
            <p className="text-xs text-slate-400 mt-1 max-w-2xl leading-relaxed">
              Expose LogShed tools and telemetry to AI assistants such as Claude Desktop, Cursor, and Windsurf
              via standard MCP protocol.
            </p>
          </div>

          <div className="flex items-center gap-3 shrink-0 self-start sm:self-center">
            <label htmlFor="enable-mcp" className="text-xs text-slate-300 font-medium cursor-pointer">
              Enable Model Context Protocol
            </label>
            <input
              id="enable-mcp"
              type="checkbox"
              aria-label="Enable Model Context Protocol"
              checked={enableMcp}
              disabled={isUpdatingMcp}
              onChange={(e) => handleToggleMcp(e.target.checked)}
              className="w-4 h-4 rounded border-dark-600 bg-dark-800 text-accent-600 focus:ring-accent-500 focus:ring-offset-dark-900 cursor-pointer disabled:opacity-50"
            />
          </div>
        </div>

        {mcpErrorMsg && (
          <div className="p-3 rounded-lg border bg-red-950/60 border-red-800 text-red-300 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{mcpErrorMsg}</span>
          </div>
        )}

        {enableMcp && (
          <div className="p-3 bg-amber-950/30 border border-amber-800/40 rounded-xl flex items-start gap-2.5 text-amber-200/90 text-xs leading-relaxed">
            <Shield className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
            <span>
              <strong className="font-semibold text-amber-300">Automated AI Redaction Notice:</strong> Log events and alert diagnoses returned to MCP clients are passed to the connected AI assistant, and through it to its provider, without prior review. Automated credential scrubbing operates on a best-effort basis and may not catch every sensitive token or secret. Ensure the logs your assistant can query do not contain unredacted secrets.
            </span>
          </div>
        )}

        {enableMcp ? (
          <div className="space-y-5 animate-in fade-in duration-150">
            {/* SSE Endpoint Field */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold text-slate-300">
                  SSE Endpoint URL
                </label>
                <span className="text-[11px] text-slate-500">
                  Network SSE Transport
                </span>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  readOnly
                  value={sseUrl}
                  className="flex-1 bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-200 select-all focus:outline-hidden focus:border-accent-500"
                />
                <button
                  type="button"
                  onClick={() => copySse(sseUrl)}
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-slate-200 bg-dark-800 hover:bg-dark-700 border border-dark-600 rounded-lg transition cursor-pointer shrink-0"
                  title="Copy SSE URL"
                >
                  {hasCopiedSse ? (
                    <>
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                      <span className="text-emerald-400">Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3.5 h-3.5" />
                      <span>Copy</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {/* Quick helper for token */}
            <div className="p-3 bg-dark-950 border border-dark-800 rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
              <div className="text-slate-400">
                <span className="text-slate-300 font-medium">Authentication: </span>
                Every network MCP client needs a Bearer token with at least{' '}
                <code className="text-accent-400 font-mono text-[11px]">logs:read</code>. The{' '}
                <span className="text-accent-400 font-mono text-[11px]">AI Assistant / MCP (Read Only)</span>{' '}
                preset lets the assistant search logs, alerts, and metrics. Choose{' '}
                <span className="text-accent-400 font-mono text-[11px]">AI Assistant / MCP + Maintenance</span>{' '}
                (adds <code className="text-accent-400 font-mono text-[11px]">maintenance:write</code>) only if
                the assistant should start and end maintenance windows: log text is untrusted and can try to steer
                an assistant.
              </div>
              <button
                type="button"
                onClick={handleOpenGenerateModal}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-700 rounded-lg transition cursor-pointer shrink-0 self-start sm:self-auto"
              >
                <Key className="w-3.5 h-3.5 text-accent-500" />
                <span>Generate API Token</span>
              </button>
            </div>

            {/* Configuration Snippets */}
            <div className="space-y-3 pt-1">
              <div className="text-xs font-medium text-slate-300">
                Client Configuration Examples:
              </div>

              {/* 1. Claude Desktop Remote via mcp-proxy */}
              <div className="bg-dark-950 border border-dark-800 rounded-lg overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggleSnippet('claude-proxy')}
                  className="w-full flex items-center justify-between p-3 bg-dark-950 hover:bg-dark-900/60 transition text-left cursor-pointer"
                  aria-expanded={!!expandedSnippets['claude-proxy']}
                >
                  <div className="flex items-center gap-2.5">
                    {expandedSnippets['claude-proxy'] ? (
                      <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    )}
                    <div className="flex flex-col">
                      <span className="text-xs font-semibold text-slate-200">
                        Claude Desktop
                      </span>
                      <span className="text-[11px] text-slate-400 font-normal">
                        (Remote via mcp-proxy)
                      </span>
                    </div>
                  </div>
                  <span className="text-[11px] text-slate-400 font-mono">
                    {expandedSnippets['claude-proxy'] ? 'Collapse' : 'Expand'}
                  </span>
                </button>

                {expandedSnippets['claude-proxy'] && (
                  <div className="p-3.5 border-t border-dark-800 bg-dark-900/30 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <p className="text-[11px] text-slate-400">
                        Add to <code className="text-slate-300 font-mono">claude_desktop_config.json</code> on your local workstation:
                      </p>
                      <button
                        type="button"
                        onClick={() => copyClaudeProxy(claudeProxyConfigSnippet)}
                        className="inline-flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 cursor-pointer"
                        title="Copy JSON configuration"
                      >
                        {hasCopiedClaudeProxy ? (
                          <>
                            <Check className="w-3 h-3 text-emerald-400" />
                            <span className="text-emerald-400">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />
                            <span>Copy</span>
                          </>
                        )}
                      </button>
                    </div>
                    <pre className="p-2.5 bg-dark-900 border border-dark-800 rounded text-[11px] font-mono text-slate-300 overflow-x-auto leading-relaxed">
                      {claudeProxyConfigSnippet}
                    </pre>
                    <p className="text-[10px] text-slate-500">
                      Note: Requires <code className="text-slate-400 font-mono">mcp-proxy</code> installed locally (Python package: <code className="text-slate-400 font-mono">uv tool install mcp-proxy</code> or <code className="text-slate-400 font-mono">pipx install mcp-proxy</code>; the npm package of the same name will not work).
                    </p>
                  </div>
                )}
              </div>

              {/* 2. Cursor / Windsurf / Antigravity Remote SSE */}
              <div className="bg-dark-950 border border-dark-800 rounded-lg overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggleSnippet('cursor-sse')}
                  className="w-full flex items-center justify-between p-3 bg-dark-950 hover:bg-dark-900/60 transition text-left cursor-pointer"
                  aria-expanded={!!expandedSnippets['cursor-sse']}
                >
                  <div className="flex items-center gap-2.5">
                    {expandedSnippets['cursor-sse'] ? (
                      <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    )}
                    <div className="flex flex-col">
                      <span className="text-xs font-semibold text-slate-200">
                        Cursor / Windsurf / Antigravity
                      </span>
                      <span className="text-[11px] text-slate-400 font-normal">
                        (Remote SSE / Streamable HTTP)
                      </span>
                    </div>
                  </div>
                  <span className="text-[11px] text-slate-400 font-mono">
                    {expandedSnippets['cursor-sse'] ? 'Collapse' : 'Expand'}
                  </span>
                </button>

                {expandedSnippets['cursor-sse'] && (
                  <div className="p-3.5 border-t border-dark-800 bg-dark-900/30 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <p className="text-[11px] text-slate-400">
                        Add to <code className="text-slate-300 font-mono">.cursor/mcp.json</code> or your client settings:
                      </p>
                      <button
                        type="button"
                        onClick={() => copyCursor(cursorConfigSnippet)}
                        className="inline-flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 cursor-pointer"
                        title="Copy JSON configuration"
                      >
                        {hasCopiedCursor ? (
                          <>
                            <Check className="w-3 h-3 text-emerald-400" />
                            <span className="text-emerald-400">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />
                            <span>Copy</span>
                          </>
                        )}
                      </button>
                    </div>
                    <pre className="p-2.5 bg-dark-900 border border-dark-800 rounded text-[11px] font-mono text-slate-300 overflow-x-auto leading-relaxed">
                      {cursorConfigSnippet}
                    </pre>
                  </div>
                )}
              </div>

              {/* 3. Claude Desktop Local Docker Stdio */}
              <div className="bg-dark-950 border border-dark-800 rounded-lg overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggleSnippet('claude-docker')}
                  className="w-full flex items-center justify-between p-3 bg-dark-950 hover:bg-dark-900/60 transition text-left cursor-pointer"
                  aria-expanded={!!expandedSnippets['claude-docker']}
                >
                  <div className="flex items-center gap-2.5">
                    {expandedSnippets['claude-docker'] ? (
                      <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    ) : (
                      <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    )}
                    <div className="flex flex-col">
                      <span className="text-xs font-semibold text-slate-200">
                        Claude Desktop
                      </span>
                      <span className="text-[11px] text-slate-400 font-normal">
                        (Local Docker Stdio)
                      </span>
                    </div>
                  </div>
                  <span className="text-[11px] text-slate-400 font-mono">
                    {expandedSnippets['claude-docker'] ? 'Collapse' : 'Expand'}
                  </span>
                </button>

                {expandedSnippets['claude-docker'] && (
                  <div className="p-3.5 border-t border-dark-800 bg-dark-900/30 space-y-2.5">
                    <div className="flex items-center justify-between">
                      <p className="text-[11px] text-slate-400">
                        Add to <code className="text-slate-300 font-mono">claude_desktop_config.json</code> when Docker runs locally:
                      </p>
                      <button
                        type="button"
                        onClick={() => copyClaudeDocker(claudeDockerConfigSnippet)}
                        className="inline-flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 cursor-pointer"
                        title="Copy JSON configuration"
                      >
                        {hasCopiedClaudeDocker ? (
                          <>
                            <Check className="w-3 h-3 text-emerald-400" />
                            <span className="text-emerald-400">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />
                            <span>Copy</span>
                          </>
                        )}
                      </button>
                    </div>
                    <pre className="p-2.5 bg-dark-900 border border-dark-800 rounded text-[11px] font-mono text-slate-300 overflow-x-auto leading-relaxed">
                      {claudeDockerConfigSnippet}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div className="p-4 bg-dark-950 border border-dark-800 rounded-lg text-xs text-slate-400 text-center">
            MCP server is currently disabled. Toggle the switch above to activate MCP endpoints and view client configuration instructions.
          </div>
        )}
      </section>

      {/* Generate Token Modal */}
      {isGenerateModalOpen && (
        <div data-testid="generate-token-modal" className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-dark-950/80 backdrop-blur-xs">
          <div className="bg-dark-900 border border-dark-700 rounded-xl shadow-2xl w-full max-w-md overflow-hidden max-h-[calc(100dvh-2rem)] sm:max-h-[90dvh] flex flex-col overscroll-contain animate-in fade-in zoom-in-95 duration-150">
            <div className="shrink-0 px-5 py-4 border-b border-dark-700 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                <Key className="w-4 h-4 text-accent-500" />
                <span>Generate API Token</span>
              </h3>
              <button
                type="button"
                onClick={() => setIsGenerateModalOpen(false)}
                className="text-slate-400 hover:text-slate-200 p-1 rounded-md transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            {modalError && (
              <div role="alert" className="mx-5 mt-4 p-2.5 rounded-lg border bg-red-950/60 border-red-800 text-red-300 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                <span>{modalError}</span>
              </div>
            )}

            <form onSubmit={handleGenerateSubmit} className="p-5 space-y-4 overflow-y-auto">
              <div>
                <label htmlFor="token-name" className="block text-xs font-medium text-slate-300 mb-1">
                  Token Name
                </label>
                <input
                  id="token-name"
                  type="text"
                  required
                  placeholder="e.g. Home Assistant or Claude Desktop"
                  value={newTokenName}
                  onChange={(e) => setNewTokenName(e.target.value)}
                  className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500"
                />
              </div>

              <TokenScopeChooser
                idPrefix="create-token"
                selectedId={selectedPresetId}
                onSelect={handleGeneratePresetSelect}
                selection={newTokenSelection}
                onSelectionChange={setNewTokenSelection}
              />

              <div>
                <label htmlFor="token-expiry" className="block text-xs font-medium text-slate-300 mb-1">
                  Expiration
                </label>
                <select
                  id="token-expiry"
                  value={expiresDays === null ? 'never' : expiresDays}
                  onChange={(e) => {
                    const val = e.target.value;
                    setExpiresDays(val === 'never' ? null : parseInt(val, 10));
                  }}
                  className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-hidden focus:border-accent-500"
                >
                  <option value="30">30 days</option>
                  <option value="90">90 days</option>
                  <option value="365">1 year</option>
                  <option value="never">No expiration</option>
                </select>
              </div>

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setIsGenerateModalOpen(false)}
                  className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-700 text-slate-300 rounded-lg transition cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting || !newTokenName.trim() || newTokenScopes.length === 0}
                  className="px-4 py-1.5 text-xs font-semibold bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white rounded-lg transition cursor-pointer"
                >
                  {isSubmitting ? 'Generating...' : 'Create Token'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Token Modal (name and scopes) */}
      {tokenToEdit && (
        <div data-testid="edit-token-modal" className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-dark-950/80 backdrop-blur-xs">
          <div className="bg-dark-900 border border-dark-700 rounded-xl shadow-2xl w-full max-w-md overflow-hidden max-h-[calc(100dvh-2rem)] sm:max-h-[90dvh] flex flex-col overscroll-contain animate-in fade-in zoom-in-95 duration-150">
            <div className="shrink-0 px-5 py-4 border-b border-dark-700 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                <Edit2 className="w-4 h-4 text-accent-500" />
                <span>Edit API token</span>
              </h3>
              <button
                type="button"
                onClick={() => setTokenToEdit(null)}
                className="text-slate-400 hover:text-slate-200 p-1 rounded-md transition"
                aria-label="Close dialog"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            {modalError && (
              <div role="alert" className="mx-5 mt-4 p-2.5 rounded-lg border bg-red-950/60 border-red-800 text-red-300 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                <span>{modalError}</span>
              </div>
            )}

            <form onSubmit={handleEditSubmit} className="p-5 space-y-4 overflow-y-auto">
              <p className="text-[11px] text-slate-400 leading-relaxed">
                Changes take effect straight away. The token string{' '}
                <code className="font-mono text-slate-300">{tokenToEdit.token_prefix}</code> stays the same, so
                clients need no update.
              </p>

              <div>
                <label htmlFor="edit-token-name" className="block text-xs font-medium text-slate-300 mb-1">
                  Name
                </label>
                <input
                  id="edit-token-name"
                  type="text"
                  required
                  maxLength={100}
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  aria-describedby={editScopesChanged && !editNameChanged ? 'edit-token-name-hint' : undefined}
                  className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500"
                />
                {editScopesChanged && !editNameChanged && (
                  <p id="edit-token-name-hint" className="mt-1 text-[11px] text-slate-400">
                    The name still says '{tokenToEdit.name}'. Rename it if it no longer fits.
                  </p>
                )}
              </div>

              <TokenScopeChooser
                idPrefix="edit-token"
                selectedId={editPresetId}
                onSelect={handleEditPresetSelect}
                selection={editSelection}
                onSelectionChange={setEditSelection}
              />

              {editGains.length > 0 && (
                <div
                  data-testid="scope-gain-warning"
                  role="status"
                  className={`p-3 rounded-lg border text-xs flex items-start gap-2 leading-relaxed ${
                    editGainsFullAccess || editGainsAssistantMaintenance
                      ? 'bg-red-950/50 border-red-800 text-red-200'
                      : 'bg-amber-950/30 border-amber-800/60 text-amber-200'
                  }`}
                >
                  <AlertTriangle
                    className={`w-4 h-4 shrink-0 mt-0.5 ${
                      editGainsFullAccess || editGainsAssistantMaintenance ? 'text-red-400' : 'text-amber-400'
                    }`}
                  />
                  <div className="space-y-1">
                    <p>
                      This token gains: <span className="font-mono font-semibold">{editGains.join(', ')}</span>.
                      Every app or assistant holding this token gets this access straight away.
                    </p>
                    {editGainsFullAccess && (
                      <p className="font-semibold">
                        Full access covers every action, including deleting expired logs, compacting the database
                        and any action added in later versions.
                      </p>
                    )}
                    {editGainsAssistantMaintenance && (
                      <p className="font-semibold">
                        An AI assistant holding this token can now start and end maintenance windows. Log text is
                        untrusted and can try to steer an assistant.
                      </p>
                    )}
                  </div>
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setTokenToEdit(null)}
                  className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-700 text-slate-300 rounded-lg transition cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!canSaveEdit}
                  className="px-4 py-1.5 text-xs font-semibold bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white rounded-lg transition cursor-pointer"
                >
                  {isSavingEdit ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* One-Time Raw Token Modal */}
      {createdRawToken && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-dark-950/85 backdrop-blur-xs">
          <div className="bg-dark-900 border border-dark-700 rounded-xl shadow-2xl w-full max-w-md overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="px-5 py-4 border-b border-dark-700 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-emerald-400 flex items-center gap-2">
                <Check className="w-4 h-4 text-emerald-400" />
                <span>Token Generated</span>
              </h3>
              <button
                type="button"
                onClick={() => setCreatedRawToken(null)}
                className="text-slate-400 hover:text-slate-200 p-1 rounded-md transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5 space-y-4">
              <div className="p-3 bg-amber-950/30 border border-amber-800/60 rounded-lg text-xs text-amber-200 space-y-1">
                <div className="font-semibold flex items-center gap-1.5 text-amber-300">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>Important Security Notice</span>
                </div>
                <p className="text-[11px] text-amber-200/90 leading-relaxed">
                  Copy this token now. It cannot be recovered once this dialog closes.
                </p>
              </div>

              <div>
                <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">
                  Bearer Token
                </label>
                <div className="flex items-center gap-2">
                  <input
                    ref={tokenInputRef}
                    type="text"
                    readOnly
                    value={createdRawToken}
                    className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs font-mono text-slate-100 select-all"
                  />
                  <button
                    type="button"
                    onClick={handleCopyRawToken}
                    className="px-3 py-2 text-xs font-semibold bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-600 rounded-lg transition flex items-center gap-1.5 shrink-0 cursor-pointer min-h-[32px] touch-manipulation select-none active:bg-dark-750"
                  >
                    {hasCopied ? (
                      <>
                        <Check className="w-3.5 h-3.5 text-emerald-400" />
                        <span className="text-emerald-400">Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>
                </div>
              </div>

              <div className="pt-2 flex justify-end">
                <button
                  type="button"
                  onClick={() => setCreatedRawToken(null)}
                  className="px-4 py-1.5 text-xs font-semibold bg-accent-600 hover:bg-accent-500 text-white rounded-lg transition cursor-pointer"
                >
                  Done
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Revoke Confirmation Modal */}
      {tokenToRevoke && (
        <div data-testid="revoke-token-modal" className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-dark-950/80 backdrop-blur-xs">
          <div className="bg-dark-900 border border-dark-700 rounded-xl shadow-2xl w-full max-w-sm overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="p-5 space-y-3">
              <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                <Trash2 className="w-4 h-4 text-red-400" />
                <span>Revoke Token</span>
              </h3>
              <p className="text-xs text-slate-300 leading-relaxed">
                Are you sure you want to revoke{' '}
                <strong className="text-white">{tokenToRevoke.name}</strong>? Any service using this
                token will immediately fail authentication with HTTP 401 errors.
              </p>
            </div>
            {modalError && (
              <div role="alert" className="mx-5 mb-4 p-2.5 rounded-lg border bg-red-950/60 border-red-800 text-red-300 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
                <span>{modalError}</span>
              </div>
            )}
            <div className="px-5 py-3.5 bg-dark-950/50 border-t border-dark-800 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setTokenToRevoke(null)}
                className="px-3 py-1.5 text-xs font-medium bg-dark-800 hover:bg-dark-700 text-slate-300 rounded-lg transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isRevoking}
                onClick={handleConfirmRevoke}
                className="px-3.5 py-1.5 text-xs font-semibold bg-red-600 hover:bg-red-500 text-white rounded-lg transition cursor-pointer disabled:opacity-50"
              >
                {isRevoking ? 'Revoking...' : 'Yes, Revoke'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ApiTokensTab;
