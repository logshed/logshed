import { apiFetch, apiFetchBlob } from './client.ts';
import {
  AlertHistoryResponse,
  AlertPreset,
  AlertRule,
  AlertRuleCreate,
  AlertRuleUpdate,
  AlertTestRequest,
  AlertTestResponse,
  MaintenanceSchedule,
  MaintenanceWindowResponse,
} from '../types.ts';

export async function fetchAlertRules(): Promise<AlertRule[]> {
  return apiFetch<AlertRule[]>('/api/alerts/rules');
}

export async function createAlertRule(data: AlertRuleCreate): Promise<AlertRule> {
  return apiFetch<AlertRule>('/api/alerts/rules', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateAlertRule(id: number, data: AlertRuleUpdate): Promise<AlertRule> {
  return apiFetch<AlertRule>('/api/alerts/rules/' + id, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteAlertRule(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/alerts/rules/' + id, {
    method: 'DELETE',
  });
}

export async function testAlertRule(data: AlertTestRequest): Promise<AlertTestResponse> {
  return apiFetch<AlertTestResponse>('/api/alerts/test', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function fetchAlertPresets(): Promise<AlertPreset[]> {
  return apiFetch<AlertPreset[]>('/api/alerts/presets');
}

export async function installAlertPreset(
  presetId: string,
  channelId?: number | null
): Promise<AlertRule> {
  return apiFetch<AlertRule>('/api/alerts/presets/' + presetId + '/install', {
    method: 'POST',
    body: JSON.stringify({ channel_id: channelId ?? null }),
  });
}

export const fetchSecurityPresets = fetchAlertPresets;
export const installSecurityPreset = installAlertPreset;


export async function fetchAlertHistory(
  limit: number = 50,
  offset: number = 0,
  ruleId?: number
): Promise<AlertHistoryResponse> {
  let url = `/api/alerts/history?limit=${limit}&offset=${offset}`;
  if (ruleId !== undefined) {
    url += `&rule_id=${ruleId}`;
  }
  return apiFetch<AlertHistoryResponse>(url);
}

export async function deleteAlertHistoryItem(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/alerts/history/' + id, {
    method: 'DELETE',
  });
}

export async function clearAlertHistory(): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/alerts/history', {
    method: 'DELETE',
  });
}

export async function exportAllAlertRules(): Promise<Blob> {
  return apiFetchBlob('/api/alerts/rules/export');
}

export async function exportSingleAlertRule(ruleId: number): Promise<Blob> {
  return apiFetchBlob(`/api/alerts/rules/${ruleId}/export`);
}

export async function importAlertRules(
  data: object
): Promise<{ imported: number; skipped: number; errors: string[] }> {
  return apiFetch<{ imported: number; skipped: number; errors: string[] }>('/api/alerts/rules/import', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function fetchMaintenanceWindow(): Promise<MaintenanceWindowResponse> {
  return apiFetch<MaintenanceWindowResponse>('/api/alerts/maintenance');
}

export async function setMaintenanceWindow(
  until: string | null
): Promise<MaintenanceWindowResponse> {
  return apiFetch<MaintenanceWindowResponse>('/api/alerts/maintenance', {
    method: 'POST',
    body: JSON.stringify({ until }),
  });
}

export async function updateMaintenanceSchedules(
  schedules: MaintenanceSchedule[]
): Promise<MaintenanceWindowResponse> {
  return apiFetch<MaintenanceWindowResponse>('/api/alerts/maintenance/schedules', {
    method: 'POST',
    body: JSON.stringify({ schedules }),
  });
}

