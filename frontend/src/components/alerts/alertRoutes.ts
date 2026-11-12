/** The Rules & History sub-tabs and their paths. */
export type AlertViewTab = 'rules' | 'drop-rules' | 'history' | 'maintenance';

export const pathToAlertSubTab = (pathname: string): AlertViewTab => {
  const clean = pathname.replace(/\/+$/, '').toLowerCase();
  if (clean === '/rules/history') {
    return 'history';
  }
  if (clean === '/rules/drop-rules' || clean === '/rules/drop') {
    return 'drop-rules';
  }
  if (clean === '/rules/maintenance') {
    return 'maintenance';
  }
  return 'rules';
};

export const alertSubTabToPath = (subTab: AlertViewTab): string => {
  switch (subTab) {
    case 'history':
      return '/rules/history';
    case 'drop-rules':
      return '/rules/drop-rules';
    case 'maintenance':
      return '/rules/maintenance';
    case 'rules':
    default:
      return '/rules/rules';
  }
};

/**
 * Id of the incident to open, from the `id` parameter of /rules/history (notification links set it
 * to the alert's history record).
 */
export function parseIncidentIdFromUrl(): number | null {
  if (pathToAlertSubTab(window.location.pathname) !== 'history') return null;
  const raw = new URLSearchParams(window.location.search).get('id');
  if (!raw || !/^\d+$/.test(raw)) return null;
  const id = parseInt(raw, 10);
  return id > 0 ? id : null;
}

export function removeIncidentIdFromUrl() {
  const params = new URLSearchParams(window.location.search);
  if (!params.has('id')) return;
  params.delete('id');
  const query = params.toString();
  window.history.replaceState(null, '', query ? `${window.location.pathname}?${query}` : window.location.pathname);
}
