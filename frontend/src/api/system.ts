import { apiFetch } from './client.ts';
import { HealthResponse, PruneResponse, StorageMetricsResponse, VacuumResponse, VersionInfo } from '../types.ts';

export async function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>('/api/health');
}

export async function fetchStorageMetrics(): Promise<StorageMetricsResponse> {
  return apiFetch<StorageMetricsResponse>('/api/system/storage');
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

