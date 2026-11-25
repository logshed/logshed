import React, { useEffect, useMemo, useState } from 'react';
import { Bot, Shield } from 'lucide-react';
import { Key } from '../common/icons.ts';
import { Badge } from '../common/Badge.tsx';
import { Button } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { CHECKBOX_CLASS, LABEL_TEXT_CLASS, TextField } from '../common/Field.tsx';
import { updateSettings } from '../../api/settings.ts';
import { SystemSettings } from '../../types.ts';
import { CopyButton } from '../common/CopyButton.tsx';
import { ConfigSnippet } from './ConfigSnippet.tsx';
import { StickySaveBar } from './StickySaveBar.tsx';
import { useSavedForm } from './useSavedForm.ts';
import { useFlash } from '../../utils/hooks.ts';

export interface McpAccessCardProps {
  settings?: Partial<SystemSettings> | null;
  onSettingsSaved?: () => void | Promise<void>;
  /** Opens the dialog that creates an API token. */
  onGenerateToken: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  /** Set to this card's save, for a navigation guard's Save & Continue. */
  saveTriggerRef?: React.MutableRefObject<(() => Promise<boolean>) | null>;
}

interface McpValues {
  enable_mcp: boolean;
}

/**
 * Settings > API & MCP Access: turns the MCP server on or off (saved with the Save Changes bar)
 * and shows how clients connect to it.
 */
export const McpAccessCard: React.FC<McpAccessCardProps> = ({
  settings,
  onSettingsSaved,
  onGenerateToken,
  onDirtyChange,
  saveTriggerRef,
}) => {
  const loaded = Boolean(settings);
  const mcpRunning = settings?.enable_mcp ?? false;
  const saved = useMemo<McpValues | null>(() => (loaded ? { enable_mcp: mcpRunning } : null), [loaded, mcpRunning]);
  const form = useSavedForm<McpValues>(saved, { enable_mcp: false });
  const enableMcp = form.values.enable_mcp;
  // The badge and the connection details (mcpRunning) show what the server runs, not an unsaved switch
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedFlash, flashSaved, setSavedFlash] = useFlash<true>(3000);

  useEffect(() => {
    onDirtyChange?.(form.isDirty);
  }, [form.isDirty, onDirtyChange]);

  // Resolves true when saved (or nothing to save), false on a failure, for a navigation guard
  const performSave = async (): Promise<boolean> => {
    const changes = form.changes();
    if (Object.keys(changes).length === 0) return true;
    setIsSaving(true);
    setSaveError(null);
    setSavedFlash(null);
    try {
      await updateSettings(changes);
      flashSaved(true);
      await onSettingsSaved?.();
      return true;
    } catch (err: any) {
      setSaveError(err.message || 'Failed to update MCP setting.');
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  useEffect(() => {
    if (saveTriggerRef) saveTriggerRef.current = performSave;
    return () => {
      if (saveTriggerRef) saveTriggerRef.current = null;
    };
  });

  const handleDiscard = () => {
    form.reset();
    setSaveError(null);
    setSavedFlash(null);
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
    <form
      className="space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        void performSave();
      }}
    >
      <Card>
        <CardHeader
          className="pb-4 border-b border-line-subtle"
          icon={<Bot />}
          title={
            <>
              Model Context Protocol (MCP)
              <Badge tone={mcpRunning ? 'success' : 'neutral'} className="ml-2 normal-case tracking-normal align-middle">
                {mcpRunning ? 'Active' : 'Disabled'}
              </Badge>
            </>
          }
          description="Expose LogShed tools and telemetry to AI assistants such as Claude Desktop, Cursor, and Windsurf via standard MCP protocol."
          actions={
            <div className="flex items-center gap-3">
              <label htmlFor="enable-mcp" className="text-xs text-fg-soft font-medium cursor-pointer">
                Enable Model Context Protocol
              </label>
              <input
                id="enable-mcp"
                type="checkbox"
                checked={enableMcp}
                disabled={isSaving}
                onChange={(e) => form.set('enable_mcp', e.target.checked)}
                className={CHECKBOX_CLASS}
              />
            </div>
          }
        />

        {enableMcp && (
          <div className="p-3 bg-warning-950/30 border border-warning-800/40 rounded-xl flex items-start gap-2.5 text-warning-200/90 text-xs leading-relaxed">
            <Shield className="w-4 h-4 text-warning-400 shrink-0 mt-0.5" />
            <span>
              <strong className="font-semibold text-warning-300">Automated AI Redaction Notice:</strong> Log events and alert diagnoses returned to MCP clients are passed to the connected AI assistant, and through it to its provider, without prior review. Automated credential scrubbing operates on a best-effort basis and may not catch every sensitive token or secret. Ensure the logs your assistant can query do not contain unredacted secrets.
            </span>
          </div>
        )}

        {mcpRunning ? (
          <div className="space-y-5">
            {/* SSE Endpoint Field */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label htmlFor="mcp-sse-url" className={LABEL_TEXT_CLASS}>
                  SSE Endpoint URL
                </label>
                <span className="text-[11px] text-fg-muted">
                  Network SSE Transport
                </span>
              </div>
              <div className="flex items-center gap-2">
                <TextField
                  id="mcp-sse-url"
                  type="text"
                  readOnly
                  value={sseUrl}
                  mono
                  className="flex-1 select-all"
                />
                <CopyButton
                  text={sseUrl}
                  title="Copy SSE URL"
                  variant="button"
                  className="shrink-0"
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
              <Button
                onClick={onGenerateToken}
                className="shrink-0 self-start sm:self-auto"
                icon={<Key className="w-3.5 h-3.5 text-accent-500" />}
              >
                Generate API Token
              </Button>
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
            MCP server is currently disabled. Tick Enable Model Context Protocol above and save to activate MCP endpoints and view client configuration instructions.
          </div>
        )}
      </Card>

      <StickySaveBar
        isDirty={form.isDirty}
        isSavingSettings={isSaving}
        saveInlineSuccess={savedFlash !== null}
        saveNotice={null}
        saveInlineError={saveError}
        onDiscard={handleDiscard}
      />
    </form>
  );
};
