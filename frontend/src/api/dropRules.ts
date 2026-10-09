import { apiFetch, apiFetchBlob } from './client.ts';
import {
  DropPreset,
  DropRule,
  DropRuleCreate,
  DropRuleUpdate,
  DropRuleTestRequest,
  DropRuleTestResponse,
} from '../types.ts';

export async function fetchDropRules(): Promise<DropRule[]> {
  return apiFetch<DropRule[]>('/api/drop-rules');
}

export async function createDropRule(data: DropRuleCreate): Promise<DropRule> {
  return apiFetch<DropRule>('/api/drop-rules', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateDropRule(
  id: number,
  data: DropRuleUpdate
): Promise<DropRule> {
  return apiFetch<DropRule>(`/api/drop-rules/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteDropRule(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/drop-rules/${id}`, {
    method: 'DELETE',
  });
}

export async function testDropRule(
  data: DropRuleTestRequest
): Promise<DropRuleTestResponse> {
  return apiFetch<DropRuleTestResponse>('/api/drop-rules/test', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function resetDropRuleCounter(id: number): Promise<DropRule> {
  return apiFetch<DropRule>(`/api/drop-rules/${id}/reset`, {
    method: 'POST',
  });
}

export async function fetchDropPresets(): Promise<DropPreset[]> {
  return apiFetch<DropPreset[]>('/api/drop-rules/presets');
}

export async function installDropPreset(presetId: string): Promise<DropRule> {
  return apiFetch<DropRule>(`/api/drop-rules/presets/${presetId}/install`, {
    method: 'POST',
  });
}

export async function exportAllDropRules(): Promise<Blob> {
  return apiFetchBlob('/api/drop-rules/export');
}

export async function exportSingleDropRule(ruleId: number): Promise<Blob> {
  return apiFetchBlob(`/api/drop-rules/${ruleId}/export`);
}

export async function importDropRules(
  data: object
): Promise<{ imported: number; skipped: number; errors: string[] }> {
  return apiFetch<{ imported: number; skipped: number; errors: string[] }>('/api/drop-rules/import', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function reorderDropRules(ruleIds: number[]): Promise<DropRule[]> {
  return apiFetch<DropRule[]>('/api/drop-rules/reorder', {
    method: 'PUT',
    body: JSON.stringify({ rule_ids: ruleIds }),
  });
}

