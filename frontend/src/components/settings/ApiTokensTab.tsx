import React, { useEffect, useState, useRef } from 'react';
import {
  Plus,
  Trash2,
  Check,
  AlertCircle,
  AlertTriangle,
  Edit2,
} from 'lucide-react';
import { Key } from '../common/icons.ts';
import { ApiToken, ApiTokenCreateRequest, ApiTokenUpdateRequest, SystemSettings } from '../../types.ts';
import { fetchApiTokens, createApiToken, revokeApiToken, updateApiToken } from '../../api/tokens.ts';
import { formatDate } from '../../utils/formatters.ts';
import { NAME_MAX_LENGTH } from '../../utils/fieldLimits.ts';

import { useMediaQuery } from '../../utils/hooks.ts';
import { TokenScopeChooser } from './TokenScopeChooser.tsx';
import { SelectDropdown, SelectOption } from '../common/SelectDropdown.tsx';
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
import { Badge } from '../common/Badge.tsx';
import { Button } from '../common/Button.tsx';
import { Card, CardHeader, SECTION_TITLE_CLASS } from '../common/Card.tsx';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { Field, LABEL_CLASS, SELECT_FIELD_CLASS, TextField } from '../common/Field.tsx';
import { Modal } from '../common/Modal.tsx';
import { TableHead } from '../common/Table.tsx';

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

/** An error inside one of this tab's dialogs. */
const ModalError: React.FC<{ message: string }> = ({ message }) => (
  <div role="alert" className="p-2.5 rounded-lg border bg-danger-950/60 border-danger-800 text-danger-300 text-xs flex items-center gap-2">
    <AlertCircle className="w-4 h-4 text-danger-400 shrink-0" />
    <span>{message}</span>
  </div>
);

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
      <Card>
        <CardHeader
          icon={<Key />}
          title="External API Access"
          description={
            <>
              Generate secure Bearer tokens for external automation, Home Assistant integrations,
              and AI assistants. All API requests authenticate via HTTP header{' '}
              <code className="text-accent-400 font-mono text-[11px] bg-canvas px-1.5 py-0.5 rounded-sm border border-line-subtle">
                Authorization: Bearer ls_live_...
              </code>
            </>
          }
          actions={
            <Button variant="primary" onClick={handleOpenGenerateModal} icon={<Plus className="w-4 h-4" />}>
              Generate Token
            </Button>
          }
        />

        {errorMsg && (
          <div className="p-3 rounded-lg border bg-danger-950/60 border-danger-800 text-danger-300 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-danger-400 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}
      </Card>

      {/* Token List Table */}
      <Card padding="flush">
        <div className="px-5 py-3.5 border-b border-line flex items-center justify-between">
          <h3 className={SECTION_TITLE_CLASS}>Active Tokens ({tokens.length})</h3>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-xs text-fg-muted font-mono">
            Loading tokens...
          </div>
        ) : tokens.length === 0 ? (
          <div className="p-8 text-center space-y-2">
            <Key className="w-8 h-8 text-fg-faint mx-auto" />
            <p className="text-xs font-medium text-fg-soft">No API tokens generated yet</p>
            <p className="text-xs text-fg-muted max-w-sm mx-auto">
              Create a token to allow Home Assistant, backup runners, or AI agents to access LogShed.
            </p>
          </div>
        ) : isMobile ? (
          /* Mobile Card View */
            <div className="block md:hidden divide-y divide-line-subtle">
              {tokens.map((token) => {
                const presetName = getPresetName(token.scopes);
                return (
                  <div key={`mobile-${token.id}`} className="p-4 space-y-2.5">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-semibold text-fg-strong">{token.name}</span>
                          <span className="font-mono text-[11px] text-fg-soft bg-canvas px-1.5 py-0.5 rounded-sm border border-line-subtle">
                            {token.token_prefix}
                          </span>
                          <Badge tone="accent">{presetName}</Badge>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <Button
                          size="sm"
                          onClick={() => handleOpenEditModal(token)}
                          title="Edit Token"
                          icon={<Edit2 className="w-3.5 h-3.5" />}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="danger-soft"
                          onClick={() => { setModalError(null); setTokenToRevoke(token); }}
                          title="Revoke Token"
                          icon={<Trash2 className="w-3.5 h-3.5" />}
                        >
                          Revoke
                        </Button>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted">
                      <span>
                        Created: <span className="text-fg-soft">{formatDate(token.created_at, serverTimezone)}</span>
                      </span>
                      <span>
                        Expires:{' '}
                        <span className="text-fg-soft">
                          {token.expires_at ? formatDate(token.expires_at, serverTimezone) : 'Never'}
                        </span>
                      </span>
                      <span>
                        Last Used:{' '}
                        <span className="text-fg-soft">
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
                <TableHead
                  columns={[
                    { label: 'Name' },
                    { label: 'Identifier' },
                    { label: 'Scopes' },
                    { label: 'Created' },
                    { label: 'Expires' },
                    { label: 'Last Used' },
                    { label: 'Actions', align: 'right' },
                  ]}
                />
                <tbody className="divide-y divide-line-subtle text-fg">
                  {tokens.map((token) => {
                    const presetName = getPresetName(token.scopes);
                    return (
                      <tr key={`desktop-${token.id}`} className="hover:bg-raised/40 transition">
                        <td className="py-3 px-4 font-medium text-fg-strong">
                          {token.name}
                        </td>
                        <td className="py-3 px-4 font-mono text-[11px] text-fg-soft">
                          {token.token_prefix}
                        </td>
                        <td className="py-3 px-4">
                          <Badge tone="accent">{presetName}</Badge>
                        </td>
                        <td className="py-3 px-4 text-fg-muted">
                          {formatDate(token.created_at, serverTimezone)}
                        </td>
                        <td className="py-3 px-4 text-fg-muted">
                          {token.expires_at ? formatDate(token.expires_at, serverTimezone) : 'Never'}
                        </td>
                        <td className="py-3 px-4 text-fg-muted">
                          {token.last_used_at ? formatDate(token.last_used_at, serverTimezone) : 'Never'}
                        </td>
                        <td className="py-3 px-4 text-right whitespace-nowrap space-x-1.5">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleOpenEditModal(token)}
                            title="Edit Token"
                            icon={<Edit2 className="w-3.5 h-3.5" />}
                          >
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="danger-soft"
                            onClick={() => { setModalError(null); setTokenToRevoke(token); }}
                            title="Revoke Token"
                            icon={<Trash2 className="w-3.5 h-3.5" />}
                          >
                            Revoke
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
        )}
      </Card>

      {/* Model Context Protocol (MCP) Section */}
      <McpAccessCard settings={settings} onSettingsSaved={onSettingsSaved} onGenerateToken={handleOpenGenerateModal} />

      {/* Generate Token Modal */}
      <Modal
        isOpen={isGenerateModalOpen}
        testId="generate-token-modal"
        title="Generate API Token"
        icon={<Key />}
        onClose={() => setIsGenerateModalOpen(false)}
        maxWidth="max-w-md"
      >
        <form onSubmit={handleGenerateSubmit} className="space-y-4">
          {modalError && <ModalError message={modalError} />}

          <TextField
            label="Token Name"
            id="token-name"
            type="text"
            required
            placeholder="e.g. Home Assistant or Claude Desktop"
            value={newTokenName}
            onChange={(e) => setNewTokenName(e.target.value)}
          />

          <TokenScopeChooser
            idPrefix="create-token"
            selectedId={selectedPresetId}
            onSelect={handleGeneratePresetSelect}
            selection={newTokenSelection}
            onSelectionChange={setNewTokenSelection}
          />

          <Field label="Expiration" id="token-expiry">
            {({ id, labelId }) => (
              <SelectDropdown
                variant="panel"
                fieldClassName={SELECT_FIELD_CLASS}
                id={id}
                labelledBy={labelId}
                label="Expiration"
                options={EXPIRY_OPTIONS}
                value={expiresDays === null ? 'never' : String(expiresDays)}
                onChange={(v) => setExpiresDays(v === 'never' ? null : parseInt(v, 10))}
              />
            )}
          </Field>

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button onClick={() => setIsGenerateModalOpen(false)}>Cancel</Button>
            <Button
              type="submit"
              variant="primary"
              disabled={isSubmitting || !newTokenName.trim() || newTokenScopes.length === 0}
            >
              {isSubmitting ? 'Generating...' : 'Create Token'}
            </Button>
          </div>
        </form>
      </Modal>

      {/* Edit Token Modal (name and scopes) */}
      <Modal
        isOpen={tokenToEdit !== null}
        testId="edit-token-modal"
        title="Edit API token"
        icon={<Edit2 />}
        onClose={() => setTokenToEdit(null)}
        maxWidth="max-w-md"
      >
        {tokenToEdit && (
          <form onSubmit={handleEditSubmit} className="space-y-4">
            {modalError && <ModalError message={modalError} />}

            <p className="text-[11px] text-fg-muted leading-relaxed">
              Changes take effect straight away. The token string{' '}
              <code className="font-mono text-fg-soft">{tokenToEdit.token_prefix}</code> stays the same, so
              clients need no update.
            </p>

            <TextField
              label="Name"
              id="edit-token-name"
              type="text"
              required
              maxLength={NAME_MAX_LENGTH}
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              hint={
                editScopesChanged && !editNameChanged
                  ? `The name still says '${tokenToEdit.name}'. Rename it if it no longer fits.`
                  : undefined
              }
            />

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
                    ? 'bg-danger-950/50 border-danger-800 text-danger-200'
                    : 'bg-warning-950/30 border-warning-800/60 text-warning-200'
                }`}
              >
                <AlertTriangle
                  className={`w-4 h-4 shrink-0 mt-0.5 ${
                    editGainsFullAccess || editGainsAssistantMaintenance ? 'text-danger-400' : 'text-warning-400'
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
              <Button onClick={() => setTokenToEdit(null)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={!canSaveEdit}>
                {isSavingEdit ? 'Saving...' : 'Save'}
              </Button>
            </div>
          </form>
        )}
      </Modal>

      {/* One-Time Raw Token Modal: the token cannot be shown again, so only Done or the close button end it */}
      <Modal
        isOpen={createdRawToken !== null}
        testId="raw-token-modal"
        title="Token Generated"
        icon={<Check />}
        onClose={() => setCreatedRawToken(null)}
        closeOnEscape={false}
        maxWidth="max-w-md"
      >
        <div className="space-y-4">
          <div className="p-3 bg-warning-950/30 border border-warning-800/60 rounded-lg text-xs text-warning-200 space-y-1">
            <div className="font-semibold flex items-center gap-1.5 text-warning-300">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>Important Security Notice</span>
            </div>
            <p className="text-[11px] text-warning-200/90 leading-relaxed">
              Copy this token now. It cannot be recovered once this dialog closes.
            </p>
          </div>

          <div>
            <label htmlFor="created-raw-token" className={LABEL_CLASS}>
              Bearer Token
            </label>
            <div className="flex items-center gap-2">
              <TextField
                id="created-raw-token"
                ref={tokenInputRef}
                type="text"
                readOnly
                value={createdRawToken ?? ''}
                mono
                className="select-all"
              />
              <CopyButton
                text={createdRawToken ?? ''}
                variant="button"
                className="shrink-0"
                copiedLabelClassName="text-success-400"
                beforeCopy={selectRawToken}
              />
            </div>
          </div>

          <div className="pt-2 flex justify-end">
            <Button variant="primary" onClick={() => setCreatedRawToken(null)}>
              Done
            </Button>
          </div>
        </div>
      </Modal>

      {/* Revoke Confirmation Modal */}
      <ConfirmDialog
        isOpen={tokenToRevoke !== null}
        testId="revoke-token-modal"
        title="Revoke Token"
        maxWidth="max-w-sm"
        onCancel={() => setTokenToRevoke(null)}
        onConfirm={handleConfirmRevoke}
        confirmDisabled={isRevoking}
        confirmLabel={isRevoking ? 'Revoking...' : 'Yes, Revoke'}
      >
        <p className="leading-relaxed">
          Are you sure you want to revoke{' '}
          <strong className="text-fg-max">{tokenToRevoke?.name}</strong>? Any service using this
          token will immediately fail authentication with HTTP 401 errors.
        </p>
        {modalError && <ModalError message={modalError} />}
      </ConfirmDialog>
    </div>
  );
};

export default ApiTokensTab;
