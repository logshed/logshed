import React from 'react';
import { ExternalLink } from 'lucide-react';
import { CHECKBOX_CLASS } from '../common/Field.tsx';
import {
  CUSTOM_PRESET_ID,
  FULL_ACCESS_SCOPE,
  SCOPE_CATALOG,
  SCOPE_PRESETS,
  collapseScopes,
  scopeGrants,
  toggleScope,
} from './tokenScopes.ts';

export interface TokenScopeChooserProps {
  /** Radio group name and id prefix, unique per modal. */
  idPrefix: string;
  /** A preset id or CUSTOM_PRESET_ID. */
  selectedId: string;
  onSelect: (id: string) => void;
  /** Ticked nodes of the custom tree (see tokenScopes.ts). */
  selection: ReadonlySet<string>;
  onSelectionChange: (next: Set<string>) => void;
}

interface ScopeCheckboxProps {
  id: string;
  label: React.ReactNode;
  description?: string;
  checked: boolean;
  indeterminate: boolean;
  onChange: (checked: boolean) => void;
}

const ScopeCheckbox: React.FC<ScopeCheckboxProps> = ({ id, label, description, checked, indeterminate, onChange }) => (
  <div className="flex items-start gap-2 py-1">
    <input
      id={id}
      type="checkbox"
      checked={checked}
      ref={(el) => {
        if (el) el.indeterminate = indeterminate;
      }}
      onChange={(e) => onChange(e.target.checked)}
      aria-describedby={description ? `${id}-desc` : undefined}
      className={`mt-0.5 ${CHECKBOX_CLASS}`}
    />
    <div className="min-w-0">
      <label htmlFor={id} className="text-xs text-fg cursor-pointer wrap-break-word">
        {label}
      </label>
      {description && (
        <p id={`${id}-desc`} className="text-[11px] text-fg-muted leading-snug">
          {description}
        </p>
      )}
    </div>
  </div>
);

const scopeCode = (scope: string) => <code className="font-mono text-[11px] text-accent-300">{scope}</code>;

export const TokenScopeChooser: React.FC<TokenScopeChooserProps> = ({
  idPrefix,
  selectedId,
  onSelect,
  selection,
  onSelectionChange,
}) => {
  const ticked = [...selection];
  const savedScopes = collapseScopes(selection);
  const nodeId = (scope: string) => `${idPrefix}-scope-${scope.replace(/[^a-z]/g, (c) => (c === '*' ? 'all' : '-'))}`;
  const change = (scope: string) => (checked: boolean) => onSelectionChange(toggleScope(selection, scope, checked));

  const choices = [
    ...SCOPE_PRESETS,
    {
      id: CUSTOM_PRESET_ID,
      name: 'Custom',
      description: 'Choose the scopes yourself when no preset fits.',
    },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span id={`${idPrefix}-scope-heading`} className="text-xs font-medium text-fg-soft">
          Scopes
        </span>
        <a
          href="https://github.com/logshed/logshed/blob/main/docs/API_SCOPES.md"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[10px] text-accent-400 hover:underline"
          title="Open the API Token Scopes Guide"
        >
          <span>Scope Guide &amp; Permissions</span>
          <ExternalLink className="w-2.5 h-2.5" />
        </a>
      </div>
      <div role="radiogroup" aria-labelledby={`${idPrefix}-scope-heading`} className="space-y-2">
        {choices.map((choice) => (
          <label
            key={choice.id}
            className={`block p-2.5 rounded-lg border text-xs cursor-pointer transition ${
              selectedId === choice.id
                ? 'bg-accent-950/20 border-accent-600/60 text-fg-strong'
                : 'bg-canvas/60 border-line-subtle text-fg-soft hover:border-line'
            }`}
          >
            <div className="flex items-start gap-2.5">
              <input
                type="radio"
                name={`${idPrefix}-preset`}
                value={choice.id}
                checked={selectedId === choice.id}
                onChange={() => onSelect(choice.id)}
                className={`mt-0.5 ${CHECKBOX_CLASS}`}
              />
              <div>
                <div className="font-semibold text-fg">{choice.name}</div>
                <p className="text-[11px] text-fg-muted mt-0.5">{choice.description}</p>
              </div>
            </div>
          </label>
        ))}
      </div>

      {selectedId === CUSTOM_PRESET_ID && (
        <fieldset className="mt-3 p-2.5 rounded-lg border border-line-subtle bg-canvas/60">
          <legend className="px-1 text-xs font-medium text-fg-soft">Custom scopes</legend>
          <ul>
            <li>
              <ScopeCheckbox
                id={nodeId(FULL_ACCESS_SCOPE)}
                label={<>Full Access ({scopeCode(FULL_ACCESS_SCOPE)})</>}
                description="Every action, including any added in later versions."
                checked={selection.has(FULL_ACCESS_SCOPE)}
                indeterminate={!selection.has(FULL_ACCESS_SCOPE) && selection.size > 0}
                onChange={change(FULL_ACCESS_SCOPE)}
              />
              <ul className="pl-4 sm:pl-5">
                {SCOPE_CATALOG.map((category) => {
                  const wildcard = `${category.id}:*`;
                  const wildcardOn = scopeGrants(ticked, wildcard);
                  const someActionOn = category.actions.some((a) => scopeGrants(ticked, a.scope));
                  return (
                    <li key={category.id}>
                      <ScopeCheckbox
                        id={nodeId(wildcard)}
                        label={
                          <>
                            All {category.id} actions ({scopeCode(wildcard)}), including any added in later versions
                          </>
                        }
                        checked={wildcardOn}
                        indeterminate={!wildcardOn && someActionOn}
                        onChange={change(wildcard)}
                      />
                      <ul className="pl-4 sm:pl-5">
                        {category.actions.map((action) => (
                          <li key={action.scope}>
                            <ScopeCheckbox
                              id={nodeId(action.scope)}
                              label={scopeCode(action.scope)}
                              description={action.description}
                              checked={scopeGrants(ticked, action.scope)}
                              indeterminate={false}
                              onChange={change(action.scope)}
                            />
                          </li>
                        ))}
                      </ul>
                    </li>
                  );
                })}
              </ul>
            </li>
          </ul>

          <div className="mt-2 pt-2 border-t border-line-subtle text-[11px]" aria-live="polite">
            {savedScopes.length === 0 ? (
              <p className="text-warning-300">Choose at least one scope</p>
            ) : (
              <p className="text-fg-muted">
                Saved as:{' '}
                <code data-testid={`${idPrefix}-saved-scopes`} className="font-mono text-fg wrap-break-word">
                  {savedScopes.join(', ')}
                </code>
              </p>
            )}
          </div>
        </fieldset>
      )}
    </div>
  );
};

export default TokenScopeChooser;
