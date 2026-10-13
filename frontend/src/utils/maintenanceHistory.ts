import { AlertHistoryItem } from '../types.ts';

export const MAINTENANCE_RULE_NAME = 'Maintenance Window';

export function isMaintenanceItem(item: AlertHistoryItem): boolean {
  return Boolean(item.maintenance_session_id);
}

export function maintenanceTypeLabel(type?: string | null): string {
  return type === 'drop_errors' ? 'Drop errors' : 'Silence alerts';
}

export function maintenanceEndLabel(item: AlertHistoryItem): string {
  if (!item.maintenance_ended_at) return 'In progress';
  return item.maintenance_end_reason === 'terminated' ? 'Ended early' : 'Expired';
}

/** One-line summary used by the incident history list. */
export function maintenanceSummary(item: AlertHistoryItem): string {
  const parts = [maintenanceTypeLabel(item.maintenance_type)];
  const scope: string[] = [];
  if (item.maintenance_target_app) scope.push(`app ${item.maintenance_target_app}`);
  if (item.maintenance_target_host) scope.push(`host ${item.maintenance_target_host}`);
  parts.push(scope.length > 0 ? scope.join(', ') : 'all apps and hosts');
  if (item.maintenance_type === 'drop_errors') {
    const dropped = item.maintenance_dropped_count ?? 0;
    parts.push(`${dropped.toLocaleString()} log${dropped === 1 ? '' : 's'} dropped`);
  }
  parts.push(maintenanceEndLabel(item).toLowerCase());
  return parts.join(' - ');
}
