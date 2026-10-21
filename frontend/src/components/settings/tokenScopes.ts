/**
 * API token scope catalog, presets and helpers shared by the create and edit token modals.
 *
 * A scope selection is the set of ticked nodes in the custom scope tree: '*' (the root), a category
 * wildcard such as 'logs:*', or an action such as 'logs:read'. A ticked node implies everything under it,
 * so a normalized selection never holds a node together with one of its ancestors.
 */

export interface ScopeAction {
  scope: string;
  description: string;
}

export interface ScopeCategory {
  id: string;
  actions: ScopeAction[];
}

export const FULL_ACCESS_SCOPE = '*';

// Keep in step with KNOWN_SCOPES in backend/app/core/api_tokens.py.
export const SCOPE_CATALOG: ScopeCategory[] = [
  {
    id: 'logs',
    actions: [
      {
        scope: 'logs:read',
        description: 'Search, count and read logs, list hosts and apps, and connect over MCP.',
      },
      { scope: 'logs:write', description: 'Send logs through the HTTP ingest API.' },
    ],
  },
  {
    id: 'alerts',
    actions: [
      { scope: 'alerts:read', description: 'Read alert and incident history, including maintenance windows.' },
    ],
  },
  {
    id: 'system',
    actions: [
      { scope: 'system:read', description: 'Read system metrics, the installed version and update status.' },
      { scope: 'system:write', description: 'Delete expired logs and compact the database (prune and vacuum).' },
    ],
  },
  {
    id: 'maintenance',
    actions: [
      { scope: 'maintenance:read', description: 'Read the status of maintenance windows.' },
      {
        scope: 'maintenance:write',
        description: 'Start and end maintenance windows, which silence alerts or drop error logs.',
      },
    ],
  },
];

export interface ScopePreset {
  id: string;
  name: string;
  description: string;
  scopes: string[];
}

export const CUSTOM_PRESET_ID = 'custom';

export const SCOPE_PRESETS: ScopePreset[] = [
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
    // The scopes the LogShed Home Assistant integration uses; it hides the controls a token lacks
    id: 'home_assistant',
    name: 'Home Assistant',
    description:
      'For the LogShed Home Assistant integration: sensors and the update entity, the Quick Maintenance switch, and the prune and vacuum buttons. For sensors only, choose Custom and tick system:read.',
    scopes: ['system:read', 'maintenance:write', 'system:write'],
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
    scopes: [FULL_ACCESS_SCOPE],
  },
];

const categoryWildcard = (category: ScopeCategory): string => `${category.id}:*`;

const categoryOf = (scope: string): ScopeCategory | undefined =>
  SCOPE_CATALOG.find((c) => scope === categoryWildcard(c) || c.actions.some((a) => a.scope === scope));

/** Every action scope in catalog order. */
export const CATALOG_ACTION_SCOPES: string[] = SCOPE_CATALOG.flatMap((c) => c.actions.map((a) => a.scope));

/** Same scopes regardless of order and duplicates. */
export function sameScopeSet(a: readonly string[], b: readonly string[]): boolean {
  const setA = new Set(a);
  const setB = new Set(b);
  return setA.size === setB.size && [...setA].every((s) => setB.has(s));
}

/** First preset granting exactly these scopes (Read Only before AI Assistant / MCP (Read Only), which share them). */
export function findMatchingPreset(scopes: readonly string[]): ScopePreset | undefined {
  return SCOPE_PRESETS.find((p) => sameScopeSet(p.scopes, scopes));
}

/**
 * Whether the scopes grant one required scope. Port of has_scope_permission in
 * backend/app/core/api_tokens.py: '*' grants everything and '<category>:*' every action in the category.
 */
export function scopeGrants(scopes: readonly string[], scope: string): boolean {
  return scopes.some(
    (granted) =>
      granted === FULL_ACCESS_SCOPE ||
      granted === scope ||
      (granted.endsWith(':*') && granted.length > 2 && scope.startsWith(granted.slice(0, -1)))
  );
}

/**
 * The shortest scope list equivalent to a selection: ['*'] when the root is ticked, otherwise each ticked
 * category as '<category>:*' and the individually ticked actions of the other categories, in catalog order.
 */
export function collapseScopes(selection: Iterable<string>): string[] {
  const ticked = new Set(selection);
  if (ticked.has(FULL_ACCESS_SCOPE)) return [FULL_ACCESS_SCOPE];
  return SCOPE_CATALOG.flatMap((category) =>
    ticked.has(categoryWildcard(category))
      ? [categoryWildcard(category)]
      : category.actions.filter((a) => ticked.has(a.scope)).map((a) => a.scope)
  );
}

/** The tree selection for a stored scope list ('logs:*' ticks the logs node), for prefilling the picker. */
export function expandScopes(scopes: readonly string[]): Set<string> {
  return new Set(collapseScopes(scopes));
}

/** The selection after ticking or unticking one node of the tree. */
export function toggleScope(selection: ReadonlySet<string>, scope: string, checked: boolean): Set<string> {
  if (scope === FULL_ACCESS_SCOPE) {
    return new Set(checked ? [FULL_ACCESS_SCOPE] : []);
  }
  const category = categoryOf(scope);
  if (!category) return new Set(selection);
  const wildcard = categoryWildcard(category);
  const actionScopes = category.actions.map((a) => a.scope);
  const next = new Set(selection);

  if (checked) {
    if (scope === wildcard) {
      actionScopes.forEach((s) => next.delete(s));
    }
    // Ticking every action one by one leaves the wildcard unticked: it would also cover later actions.
    next.add(scope);
    return expandScopes([...next]);
  }

  // Unticking a node under a ticked ancestor unticks the ancestor and keeps the node's siblings ticked.
  if (next.delete(FULL_ACCESS_SCOPE)) {
    SCOPE_CATALOG.forEach((c) => next.add(categoryWildcard(c)));
  }
  if (scope === wildcard) {
    next.delete(wildcard);
    actionScopes.forEach((s) => next.delete(s));
    return next;
  }
  if (next.delete(wildcard)) {
    actionScopes.forEach((s) => next.add(s));
  }
  next.delete(scope);
  return next;
}

/**
 * What newScopes grants that oldScopes does not, in catalog order: '*' or a category wildcard the old list
 * did not grant (they also cover later actions), and catalog actions not already covered by those.
 */
export function addedAccess(oldScopes: readonly string[], newScopes: readonly string[]): string[] {
  const gained = (scope: string) => scopeGrants(newScopes, scope) && !scopeGrants(oldScopes, scope);
  if (gained(FULL_ACCESS_SCOPE)) return [FULL_ACCESS_SCOPE];
  return SCOPE_CATALOG.flatMap((category) => {
    const wildcard = categoryWildcard(category);
    if (gained(wildcard)) return [wildcard];
    return category.actions.filter((a) => gained(a.scope)).map((a) => a.scope);
  });
}
