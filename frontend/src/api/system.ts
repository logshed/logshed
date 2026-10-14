import { apiFetch } from './client.ts';
import { HealthResponse, PrunePreview, PruneResponse, StorageMetricsResponse, VacuumResponse, VersionInfo } from '../types.ts';

export async function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>('/api/health');
}

export async function fetchStorageMetrics(): Promise<StorageMetricsResponse> {
  return apiFetch<StorageMetricsResponse>('/api/system/storage');
}

export async function fetchPrunePreview(retentionDays?: number): Promise<PrunePreview> {
  const query = retentionDays === undefined ? '' : `?retention_days=${retentionDays}`;
  return apiFetch<PrunePreview>(`/api/maintenance/prune/preview${query}`);
}

export async function triggerManualPrune(): Promise<PruneResponse> {
  return apiFetch<PruneResponse>('/api/maintenance/prune', {
    method: 'POST',
  });
}

export async function triggerVacuum(): Promise<VacuumResponse> {
  return apiFetch<VacuumResponse>('/api/system/vacuum', {
    method: 'POST',
  });
}

export async function fetchVersion(refresh: boolean = false): Promise<VersionInfo> {
  const query = refresh ? '?refresh=true' : '';
  return apiFetch<VersionInfo>(`/api/system/version${query}`);
}

