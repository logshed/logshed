import { describe, it, expect } from 'vitest';
import {
  CATALOG_ACTION_SCOPES,
  SCOPE_PRESETS,
  addedAccess,
  collapseScopes,
  expandScopes,
  findMatchingPreset,
  sameScopeSet,
  scopeGrants,
  toggleScope,
} from '../components/settings/tokenScopes.ts';

// KNOWN_SCOPES in backend/app/core/api_tokens.py (test_api_tokens.py checks the catalog against it)
const KNOWN_SCOPES = [
  'logs:read',
  'logs:write',
  'alerts:read',
  'system:read',
  'system:write',
  'maintenance:read',
  'maintenance:write',
];

describe('tokenScopes', () => {
  it('lists exactly the backend KNOWN_SCOPES in the catalog', () => {
    expect([...CATALOG_ACTION_SCOPES].sort()).toEqual([...KNOWN_SCOPES].sort());
    expect(new Set(CATALOG_ACTION_SCOPES).size).toBe(CATALOG_ACTION_SCOPES.length);
  });

  it('scopeGrants follows has_scope_permission', () => {
    expect(scopeGrants(['*'], 'maintenance:write')).toBe(true);
    expect(scopeGrants(['*'], 'logs:*')).toBe(true);
    expect(scopeGrants(['logs:*'], 'logs:write')).toBe(true);
    expect(scopeGrants(['logs:*'], 'logs:*')).toBe(true);
    expect(scopeGrants(['logs:*'], 'alerts:read')).toBe(false);
    expect(scopeGrants(['logs:read'], 'logs:read')).toBe(true);
    expect(scopeGrants(['logs:read', 'logs:write'], 'logs:*')).toBe(false);
    expect(scopeGrants(['logs:*'], 'logsx:read')).toBe(false);
    expect(scopeGrants([':*'], 'logs:read')).toBe(false);
    expect(scopeGrants([], 'logs:read')).toBe(false);
  });

  it('collapseScopes returns the shortest list in catalog order', () => {
    expect(collapseScopes(['logs:read', '*'])).toEqual(['*']);
    expect(collapseScopes(['logs:*', 'logs:read', 'logs:write'])).toEqual(['logs:*']);
    expect(collapseScopes(['maintenance:write', 'alerts:*', 'logs:read'])).toEqual([
      'logs:read',
      'alerts:*',
      'maintenance:write',
    ]);
    // Every action ticked one by one is not the wildcard
    expect(collapseScopes(['logs:write', 'logs:read'])).toEqual(['logs:read', 'logs:write']);
    expect(collapseScopes([])).toEqual([]);
  });

  it('expandScopes prefills the tree from a stored list', () => {
    expect(expandScopes(['logs:*'])).toEqual(new Set(['logs:*']));
    expect(expandScopes(['*'])).toEqual(new Set(['*']));
    expect(expandScopes(['logs:read', 'logs:*', 'alerts:read'])).toEqual(new Set(['logs:*', 'alerts:read']));
  });

  it('toggleScope ticks and unticks nodes of the tree', () => {
    // Ticking a category stores its wildcard and absorbs its ticked actions
    expect(collapseScopes(toggleScope(new Set(['logs:read']), 'logs:*', true))).toEqual(['logs:*']);
    // Unticking a child of a ticked category keeps its siblings ticked one by one
    expect(collapseScopes(toggleScope(new Set(['logs:*']), 'logs:write', false))).toEqual(['logs:read']);
    // Unticking a child of the ticked root keeps every other category as a wildcard
    expect(collapseScopes(toggleScope(new Set(['*']), 'logs:write', false))).toEqual([
      'logs:read',
      'alerts:*',
      'system:*',
      'maintenance:*',
    ]);
    expect(collapseScopes(toggleScope(new Set(['*']), 'system:*', false))).toEqual([
      'logs:*',
      'alerts:*',
      'maintenance:*',
    ]);
    // Ticking the last action does not tick the category
    expect(collapseScopes(toggleScope(new Set(['logs:read']), 'logs:write', true))).toEqual([
      'logs:read',
      'logs:write',
    ]);
    expect(toggleScope(new Set(['logs:read', 'alerts:*']), '*', true)).toEqual(new Set(['*']));
    expect(toggleScope(new Set(['*']), '*', false)).toEqual(new Set());
    expect(toggleScope(new Set(['logs:*', 'alerts:read']), 'logs:*', false)).toEqual(new Set(['alerts:read']));
  });

  it('addedAccess lists actions and wildcards the old list did not grant', () => {
    expect(addedAccess(['logs:read', 'alerts:read', 'system:read'], ['logs:read', 'alerts:read', 'system:read', 'maintenance:write'])).toEqual([
      'maintenance:write',
    ]);
    // A wildcard is gained even when every current action was already granted
    expect(addedAccess(['logs:read', 'logs:write'], ['logs:*'])).toEqual(['logs:*']);
    expect(addedAccess(['logs:read'], ['*'])).toEqual(['*']);
    expect(addedAccess(['*'], ['logs:read'])).toEqual([]);
    expect(addedAccess(['logs:*'], ['logs:read', 'logs:write'])).toEqual([]);
    expect(addedAccess(['logs:read'], ['logs:write'])).toEqual(['logs:write']);
  });

  it('matches presets by exact scope set', () => {
    expect(sameScopeSet(['a', 'b'], ['b', 'a', 'a'])).toBe(true);
    expect(sameScopeSet(['a'], ['a', 'b'])).toBe(false);
    expect(findMatchingPreset(['system:read', 'logs:read', 'alerts:read'])?.id).toBe('readonly');
    expect(findMatchingPreset(['logs:read', 'alerts:read', 'system:read', 'logs:write'])).toBeUndefined();
    expect(findMatchingPreset(['system:write', 'maintenance:write', 'system:read'])?.id).toBe('home_assistant');
    for (const preset of SCOPE_PRESETS) {
      expect(preset.scopes.every((s) => s === '*' || KNOWN_SCOPES.includes(s))).toBe(true);
    }
  });
});
