import React, { useEffect, useId, useState, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  Key,
  Plus,
  Trash2,
  Check,
  AlertCircle,
  AlertTriangle,
  X,
  Edit2,
} from 'lucide-react';
import { ApiToken, ApiTokenCreateRequest, ApiTokenUpdateRequest, SystemSettings } from '../../types.ts';
import { fetchApiTokens, createApiToken, revokeApiToken, updateApiToken } from '../../api/tokens.ts';
import { formatDate } from '../../utils/formatters.ts';
import { NAME_MAX_LENGTH } from '../../utils/fieldLimits.ts';

import { useMediaQuery } from '../../utils/hooks.ts';
import { TokenScopeChooser } from './TokenScopeChooser.tsx';
import { SelectDropdown, SelectOption } from '../common/SelectDropdown.tsx';
import { useDialog } from '../../utils/dialogStack.ts';
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
import { CopyButton } from '../common/CopyButton.tsx';
import { McpAccessCard } from './McpAccessCard.tsx';

const DEFAULT_PRESET_ID = 'ai_mcp';

const EXPIRY_OPTIONS: SelectOption[] = [
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '365', label: '1 year' },
  { value: 'never', label: 'No expiration' },
];

/** The scopes a modal would save: the chosen preset's list, or the collapsed custom selection. */
const chosenScopes = (presetId: string, selection: ReadonlySet<string>): string[] =>
  presetId === CUSTOM_PRESET_ID
    ? collapseScopes(selection)
    : (SCOPE_PRESETS.find((p) => p.id === presetId)?.scopes ?? []);

// Exact match against SCOPE_PRESETS: the Read Only and AI Assistant / MCP (Read Only) presets grant the
// same scopes and show as "Read Only"; any other combination is "Custom".
export const getPresetName = (scopes: string[]): string => findMatchingPreset(scopes)?.name ?? 'Custom';

interface TokenDialogProps {
  titleId: string;
  onClose: () => void;
  closeOnEscape?: boolean;
  testId?: string;
  overlayClassName?: string;
  panelClassName?: string;
  children: React.ReactNode;
}

/**
 * The overlay and panel of this tab's dialogs, on the shared dialog stack (focus trap, Esc, focus return).
 * Rendered on the page body, so the tab's child spacing cannot push the overlay down from the top edge.
 */
const TokenDialog: React.FC<TokenDialogProps> = ({
  titleId,
  onClose,
  closeOnEscape = true,
  testId,
  overlayClassName = 'bg-dark-950/80',
  panelClassName = '',
  children,
}) => {
  const panelRef = useRef<HTMLDivElement>(null);
  useDialog(true, onClose, panelRef, { closeOnEscape });
  return createPortal(
    <div data-testid={testId} className={`fixed inset-0 z-50 flex items-center justify-center p-4 ${overlayClassName} backdrop-blur-xs`}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`bg-dark-900 border border-dark-700 rounded-xl shadow-2xl w-full overflow-hidden outline-none animate-in fade-in zoom-in-95 duration-150 ${panelClassName}`}
      >
        {children}
      </div>
    </div>,
    document.body
  );
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
  const serverTimezone = settings?.server_timezone || undefined;
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

  // Ids for the dialogs' titles (aria-labelledby)
  const dialogIdBase = useId();

  // One-time created token modal
  const [createdRawToken, setCreatedRawToken] = useState<string | null>(null);
  const tokenInputRef = useRef<HTMLInputElement>(null);

  // Revoke confirmation modal
  const [tokenToRevoke, setTokenToRevoke] = useState<ApiToken | null>(null);
  const [isRevoking, setIsRevoking] = useState<boolean>(false);

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

  // Selects the new token in its field as it is copied, so the user sees what was copied
  const selectRawToken = () => {
    if (tokenInputRef.current) {
      tokenInputRef.current.focus();
      tokenInputRef.current.select();
      tokenInputRef.current.setSelectionRange(0, 99999);
    }
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
            className="inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold bg-accent-700 hover:bg-accent-600 text-white rounded-lg transition shadow-xs cursor-pointer shrink-0 self-start sm:self-auto"
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
            <p className="text-xs text-slate-400 max-w-sm mx-auto">
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
                        Created: <span className="text-slate-300">{formatDate(token.created_at, serverTimezone)}</span>
                      </span>
                      <span>
                        Expires:{' '}
                        <span className="text-slate-300">
                          {token.expires_at ? formatDate(token.expires_at, serverTimezone) : 'Never'}
                        </span>
                      </span>
                      <span>
                        Last Used:{' '}
                        <span className="text-slate-300">
                          {token.last_used_at ? formatDate(token.last_used_at, serverTimezone) : 'Never'}
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
                          {formatDate(token.created_at, serverTimezone)}
                        </td>
                        <td className="py-3 px-4 text-slate-400">
                          {token.expires_at ? formatDate(token.expires_at, serverTimezone) : (
                            <span className="text-slate-400">Never</span>
                          )}
                        </td>
                        <td className="py-3 px-4 text-slate-400">
                          {token.last_used_at ? formatDate(token.last_used_at, serverTimezone) : (
                            <span className="text-slate-400">Never</span>
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
      <McpAccessCard settings={settings} onSettingsSaved={onSettingsSaved} onGenerateToken={handleOpenGenerateModal} />

      {/* Generate Token Modal */}
      {isGenerateModalOpen && (
        <TokenDialog
          testId="generate-token-modal"
          titleId={`${dialogIdBase}-generate`}
          onClose={() => setIsGenerateModalOpen(false)}
          panelClassName="max-w-md max-h-[calc(100dvh-2rem)] sm:max-h-[90dvh] flex flex-col overscroll-contain"
        >
            <div className="shrink-0 px-5 py-4 border-b border-dark-700 flex items-center justify-between">
              <h2 id={`${dialogIdBase}-generate`} className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                <Key className="w-4 h-4 text-accent-500" />
                <span>Generate API Token</span>
              </h2>
              <button
                type="button"
                onClick={() => setIsGenerateModalOpen(false)}
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
                  className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-accent-500"
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
                <label id="token-expiry-label" htmlFor="token-expiry" className="block text-xs font-medium text-slate-300 mb-1">
                  Expiration
                </label>
                <SelectDropdown
                  variant="panel"
                  fieldClassName="bg-dark-950 border-dark-700 rounded-lg px-3 py-2 text-slate-200"
                  id="token-expiry"
                  labelledBy="token-expiry-label"
                  label="Expiration"
                  options={EXPIRY_OPTIONS}
                  value={expiresDays === null ? 'never' : String(expiresDays)}
                  onChange={(v) => setExpiresDays(v === 'never' ? null : parseInt(v, 10))}
                />
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
                  className="px-4 py-1.5 text-xs font-semibold bg-accent-700 hover:bg-accent-600 disabled:opacity-50 text-white rounded-lg transition cursor-pointer"
                >
                  {isSubmitting ? 'Generating...' : 'Create Token'}
                </button>
              </div>
            </form>
        </TokenDialog>
      )}

      {/* Edit Token Modal (name and scopes) */}
      {tokenToEdit && (
        <TokenDialog
          testId="edit-token-modal"
          titleId={`${dialogIdBase}-edit`}
          onClose={() => setTokenToEdit(null)}
          panelClassName="max-w-md max-h-[calc(100dvh-2rem)] sm:max-h-[90dvh] flex flex-col overscroll-contain"
        >
            <div className="shrink-0 px-5 py-4 border-b border-dark-700 flex items-center justify-between">
              <h2 id={`${dialogIdBase}-edit`} className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                <Edit2 className="w-4 h-4 text-accent-500" />
                <span>Edit API token</span>
              </h2>
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
                  maxLength={NAME_MAX_LENGTH}
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  aria-describedby={editScopesChanged && !editNameChanged ? 'edit-token-name-hint' : undefined}
                  className="w-full bg-dark-950 border border-dark-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-accent-500"
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
                  className="px-4 py-1.5 text-xs font-semibold bg-accent-700 hover:bg-accent-600 disabled:opacity-50 text-white rounded-lg transition cursor-pointer"
                >
                  {isSavingEdit ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
        </TokenDialog>
      )}

      {/* One-Time Raw Token Modal */}
      {createdRawToken && (
        <TokenDialog
          testId="raw-token-modal"
          titleId={`${dialogIdBase}-created`}
          onClose={() => setCreatedRawToken(null)}
          // The token cannot be shown again, so only Done or the close button end this dialog
          closeOnEscape={false}
          overlayClassName="bg-dark-950/85"
          panelClassName="max-w-md"
        >
            <div className="px-5 py-4 border-b border-dark-700 flex items-center justify-between">
              <h2 id={`${dialogIdBase}-created`} className="text-sm font-semibold text-emerald-400 flex items-center gap-2">
                <Check className="w-4 h-4 text-emerald-400" />
                <span>Token Generated</span>
              </h2>
              <button
                type="button"
                onClick={() => setCreatedRawToken(null)}
                className="text-slate-400 hover:text-slate-200 p-1 rounded-md transition"
                aria-label="Close dialog"
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
                  <CopyButton
                    text={createdRawToken}
                    className="px-3 py-2 text-xs font-semibold bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-600 rounded-lg transition flex items-center gap-1.5 shrink-0 cursor-pointer min-h-[32px] touch-manipulation select-none active:bg-dark-750"
                    copiedLabelClassName="text-emerald-400"
                    beforeCopy={selectRawToken}
                  />
                </div>
              </div>

              <div className="pt-2 flex justify-end">
                <button
                  type="button"
                  onClick={() => setCreatedRawToken(null)}
                  className="px-4 py-1.5 text-xs font-semibold bg-accent-700 hover:bg-accent-600 text-white rounded-lg transition cursor-pointer"
                >
                  Done
                </button>
              </div>
            </div>
        </TokenDialog>
      )}

      {/* Revoke Confirmation Modal */}
      {tokenToRevoke && (
        <TokenDialog
          testId="revoke-token-modal"
          titleId={`${dialogIdBase}-revoke`}
          onClose={() => setTokenToRevoke(null)}
          panelClassName="max-w-sm"
        >
            <div className="p-5 space-y-3">
              <h2 id={`${dialogIdBase}-revoke`} className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                <Trash2 className="w-4 h-4 text-red-400" />
                <span>Revoke Token</span>
              </h2>
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
        </TokenDialog>
      )}
    </div>
  );
};

export default ApiTokensTab;
