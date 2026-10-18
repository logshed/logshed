import React, { useEffect, useState, useRef } from 'react';
import {
  Key,
  Plus,
  Trash2,
  Copy,
  Check,
  AlertCircle,
  X,
  ExternalLink,
  Bot,
  Shield,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { ApiToken, ApiTokenCreateRequest, SystemSettings } from '../../types.ts';
import { fetchApiTokens, createApiToken, revokeApiToken } from '../../api/tokens.ts';
import { updateSettings } from '../../api/settings.ts';
import { formatDate } from '../../utils/formatters.ts';

import { useClipboard, useMediaQuery } from '../../utils/hooks.ts';

interface ScopePreset {
  id: string;
  name: string;
  description: string;
  scopes: string[];
}

const SCOPE_PRESETS: ScopePreset[] = [
  {
    id: 'maintenance',
    name: 'Maintenance Only',
    description: 'Recommended for standalone backup scripts and automation hooks.',
    scopes: ['maintenance:write', 'maintenance:read'],
  },
  {
    id: 'ingest',
    name: 'Edge Shipper / Ingest',
    description: 'Recommended for Vector, Fluent Bit, and external HTTP log shippers.',
    scopes: ['logs:write'],
  },
  {
    id: 'readonly',
    name: 'Read Only',
    description: 'Recommended for external dashboards and read-only telemetry.',
    scopes: ['logs:read', 'alerts:read', 'system:read'],
  },
  {
    // Default preset of the token dialog: least privilege for AI assistants
    id: 'ai_mcp',
    name: 'AI Assistant / MCP (Read Only)',
    description:
      'Recommended for Claude Desktop, Cursor, and other MCP clients. The assistant can search logs, alerts, and metrics but cannot start or end maintenance windows.',
    scopes: ['logs:read', 'alerts:read', 'system:read'],
  },
  {
    id: 'ai_mcp_maintenance',
    name: 'AI Assistant / MCP + Maintenance',
    description:
      'Adds maintenance:write so the assistant can start and end maintenance windows. Choose it only when needed: log text is untrusted and can try to steer an assistant.',
    scopes: ['logs:read', 'alerts:read', 'system:read', 'maintenance:write'],
  },
  {
    id: 'full',
    name: 'Full Access',
    description: 'Recommended for administrative integrations with full system capabilities.',
    scopes: ['*'],
  },
];

export const getPresetName = (scopes: string[]): string => {
  if (scopes.includes('*')) return 'Full Access';
  const hasMaintWrite = scopes.includes('maintenance:write');
  const hasLogsRead = scopes.includes('logs:read');
  const hasAlertsRead = scopes.includes('alerts:read');
  const hasSystemRead = scopes.includes('system:read');
  const hasLogsWrite = scopes.includes('logs:write');

  if (hasMaintWrite && hasLogsRead && hasAlertsRead && hasSystemRead) {
    return 'AI Assistant / MCP + Maintenance';
  }
  // Shared by the Read Only and AI Assistant / MCP (Read Only) presets, which grant the same scopes
  if (hasLogsRead && hasAlertsRead && hasSystemRead && !hasMaintWrite) {
    return 'Read Only';
  }
  if (hasMaintWrite && scopes.includes('maintenance:read') && !hasLogsRead) {
    return 'Maintenance Only';
  }
  if (hasLogsWrite && scopes.length === 1) {
    return 'Edge Shipper / Ingest';
  }
  return scopes.join(', ');
};

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
  const [selectedPresetId, setSelectedPresetId] = useState<string>('ai_mcp');
  const [expiresDays, setExpiresDays] = useState<number | null>(90);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

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
    setSelectedPresetId('ai_mcp');
    setExpiresDays(90);
    setIsGenerateModalOpen(true);
  };

  const handleGenerateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTokenName.trim()) return;

    const preset = SCOPE_PRESETS.find((p) => p.id === selectedPresetId) || SCOPE_PRESETS.find((p) => p.id === 'ai_mcp') || SCOPE_PRESETS[0];
    setIsSubmitting(true);
    setErrorMsg(null);
    setModalError(null);

    try {
      const payload: ApiTokenCreateRequest = {
        name: newTokenName.trim(),
        scopes: preset.scopes,
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
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-dark-800 text-accent-300 border border-dark-700">
                            {presetName}
                          </span>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => { setModalError(null); setTokenToRevoke(token); }}
                        className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium text-red-400 hover:text-red-300 hover:bg-red-950/40 border border-dark-700 hover:border-red-900 rounded-lg transition cursor-pointer shrink-0"
                        title="Revoke Token"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        <span>Revoke</span>
                      </button>
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
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-dark-800 text-accent-300 border border-dark-700">
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
                        <td className="py-3 px-4 text-right">
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
          <div className="bg-dark-900 border border-dark-700 rounded-xl shadow-2xl w-full max-w-md overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            <div className="px-5 py-4 border-b border-dark-700 flex items-center justify-between">
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

            <form onSubmit={handleGenerateSubmit} className="p-5 space-y-4">
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

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-xs font-medium text-slate-300">
                    Scope Preset
                  </label>
                  <a
                    href="https://github.com/logshed/logshed/blob/main/docs/spec/security.md#8-external-api-tokens--permissions"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-[10px] text-accent-400 hover:underline"
                    title="Open API Scopes Documentation"
                  >
                    <span>Scope Guide &amp; Permissions</span>
                    <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                </div>
                <div className="space-y-2">
                  {SCOPE_PRESETS.map((preset) => (
                    <label
                      key={preset.id}
                      className={`block p-2.5 rounded-lg border text-xs cursor-pointer transition ${
                        selectedPresetId === preset.id
                          ? 'bg-accent-950/20 border-accent-600/60 text-slate-100'
                          : 'bg-dark-950/60 border-dark-800 text-slate-300 hover:border-dark-700'
                      }`}
                    >
                      <div className="flex items-start gap-2.5">
                        <input
                          type="radio"
                          name="preset"
                          value={preset.id}
                          checked={selectedPresetId === preset.id}
                          onChange={() => setSelectedPresetId(preset.id)}
                          className="mt-0.5 text-accent-500 focus:ring-accent-500"
                        />
                        <div>
                          <div className="font-semibold text-slate-200">{preset.name}</div>
                          <p className="text-[11px] text-slate-400 mt-0.5">{preset.description}</p>
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

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
                  disabled={isSubmitting || !newTokenName.trim()}
                  className="px-4 py-1.5 text-xs font-semibold bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white rounded-lg transition cursor-pointer"
                >
                  {isSubmitting ? 'Generating...' : 'Create Token'}
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
