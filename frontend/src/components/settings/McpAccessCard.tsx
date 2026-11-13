import React, { useEffect, useState } from 'react';
import { AlertCircle, Bot, Shield } from 'lucide-react';
import { Key } from '../common/icons.ts';
import { updateSettings } from '../../api/settings.ts';
import { SystemSettings } from '../../types.ts';
import { CopyButton } from '../common/CopyButton.tsx';
import { ConfigSnippet } from './ConfigSnippet.tsx';

export interface McpAccessCardProps {
  settings?: Partial<SystemSettings> | null;
  onSettingsSaved?: () => void | Promise<void>;
  /** Opens the dialog that creates an API token. */
  onGenerateToken: () => void;
}

/** Settings > API & MCP Access: turns the MCP server on or off and shows how clients connect to it. */
export const McpAccessCard: React.FC<McpAccessCardProps> = ({ settings, onSettingsSaved, onGenerateToken }) => {
  // MCP settings state
  const [enableMcp, setEnableMcp] = useState<boolean>(settings?.enable_mcp ?? false);
  const [isUpdatingMcp, setIsUpdatingMcp] = useState<boolean>(false);
  const [mcpErrorMsg, setMcpErrorMsg] = useState<string | null>(null);

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

  return (
    <section className="bg-surface border border-line rounded-xl p-3.5 sm:p-5 shadow-md space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-line-subtle">
        <div>
          <h3 className="text-xs font-semibold text-fg-soft uppercase tracking-wider flex items-center gap-2">
            <Bot className="w-4 h-4 text-accent-500" />
            <span>Model Context Protocol (MCP)</span>
            {enableMcp ? (
              <span className="inline-flex items-center px-2 py-0.5 rounded-sm text-[10px] font-semibold bg-success-950/80 text-success-400 border border-success-800/80 normal-case tracking-normal">
                Active
              </span>
            ) : (
              <span className="inline-flex items-center px-2 py-0.5 rounded-sm text-[10px] font-semibold bg-raised text-fg-muted border border-line normal-case tracking-normal">
                Disabled
              </span>
            )}
          </h3>
          <p className="text-xs text-fg-muted mt-1 max-w-2xl leading-relaxed">
            Expose LogShed tools and telemetry to AI assistants such as Claude Desktop, Cursor, and Windsurf
            via standard MCP protocol.
          </p>
        </div>

        <div className="flex items-center gap-3 shrink-0 self-start sm:self-center">
          <label htmlFor="enable-mcp" className="text-xs text-fg-soft font-medium cursor-pointer">
            Enable Model Context Protocol
          </label>
          <input
            id="enable-mcp"
            type="checkbox"
            aria-label="Enable Model Context Protocol"
            checked={enableMcp}
            disabled={isUpdatingMcp}
            onChange={(e) => handleToggleMcp(e.target.checked)}
            className="w-4 h-4 rounded-sm border-line-strong bg-raised text-accent-600 focus:ring-accent-500 focus:ring-offset-surface cursor-pointer disabled:opacity-50"
          />
        </div>
      </div>

      {mcpErrorMsg && (
        <div className="p-3 rounded-lg border bg-danger-950/60 border-danger-800 text-danger-300 text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-danger-400 shrink-0" />
          <span>{mcpErrorMsg}</span>
        </div>
      )}

      {enableMcp && (
        <div className="p-3 bg-warning-950/30 border border-warning-800/40 rounded-xl flex items-start gap-2.5 text-warning-200/90 text-xs leading-relaxed">
          <Shield className="w-4 h-4 text-warning-400 shrink-0 mt-0.5" />
          <span>
            <strong className="font-semibold text-warning-300">Automated AI Redaction Notice:</strong> Log events and alert diagnoses returned to MCP clients are passed to the connected AI assistant, and through it to its provider, without prior review. Automated credential scrubbing operates on a best-effort basis and may not catch every sensitive token or secret. Ensure the logs your assistant can query do not contain unredacted secrets.
          </span>
        </div>
      )}

      {enableMcp ? (
        <div className="space-y-5 duration-150">
          {/* SSE Endpoint Field */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold text-fg-soft">
                SSE Endpoint URL
              </label>
              <span className="text-[11px] text-fg-muted">
                Network SSE Transport
              </span>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="text"
                readOnly
                value={sseUrl}
                className="flex-1 bg-canvas border border-line rounded-lg px-3 py-2 text-xs font-mono text-fg select-all focus:outline-hidden focus:border-accent-500"
              />
              <CopyButton
                text={sseUrl}
                title="Copy SSE URL"
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-fg bg-raised hover:bg-raised-strong border border-line-strong rounded-lg transition cursor-pointer shrink-0"
                copiedLabelClassName="text-success-400"
              />
            </div>
          </div>

          {/* Quick helper for token */}
          <div className="p-3 bg-canvas border border-line-subtle rounded-lg flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
            <div className="text-fg-muted">
              <span className="text-fg-soft font-medium">Authentication: </span>
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
              onClick={onGenerateToken}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium bg-raised hover:bg-raised-strong text-fg border border-line rounded-lg transition cursor-pointer shrink-0 self-start sm:self-auto"
            >
              <Key className="w-3.5 h-3.5 text-accent-500" />
              <span>Generate API Token</span>
            </button>
          </div>

          {/* Configuration Snippets */}
          <div className="space-y-3 pt-1">
            <div className="text-xs font-medium text-fg-soft">
              Client Configuration Examples:
            </div>

            {/* 1. Claude Desktop Remote via mcp-proxy */}
            <ConfigSnippet
              title="Claude Desktop"
              subtitle="(Remote via mcp-proxy)"
              instruction={<>Add to <code className="text-fg-soft font-mono">claude_desktop_config.json</code> on your local workstation:</>}
              snippet={claudeProxyConfigSnippet}
              note={<>Note: Requires <code className="text-fg-muted font-mono">mcp-proxy</code> installed locally (Python package: <code className="text-fg-muted font-mono">uv tool install mcp-proxy</code> or <code className="text-fg-muted font-mono">pipx install mcp-proxy</code>; the npm package of the same name will not work).</>}
            />

            {/* 2. Cursor / Windsurf / Antigravity Remote SSE */}
            <ConfigSnippet
              title="Cursor / Windsurf / Antigravity"
              subtitle="(Remote SSE / Streamable HTTP)"
              instruction={<>Add to <code className="text-fg-soft font-mono">.cursor/mcp.json</code> or your client settings:</>}
              snippet={cursorConfigSnippet}
            />

            {/* 3. Claude Desktop Local Docker Stdio */}
            <ConfigSnippet
              title="Claude Desktop"
              subtitle="(Local Docker Stdio)"
              instruction={<>Add to <code className="text-fg-soft font-mono">claude_desktop_config.json</code> when Docker runs locally:</>}
              snippet={claudeDockerConfigSnippet}
            />
          </div>
        </div>
      ) : (
        <div className="p-4 bg-canvas border border-line-subtle rounded-lg text-xs text-fg-muted text-center">
          MCP server is currently disabled. Toggle the switch above to activate MCP endpoints and view client configuration instructions.
        </div>
      )}
    </section>
  );
};
