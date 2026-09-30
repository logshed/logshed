import { apiFetch } from './client.ts';
import {
  LogEntry,
  LogFilterParams,
  LogContextResponse,
  LogDeleteParams,
  LogDeleteResponse,
  LogDeletePreviewResponse,
} from '../types.ts';

export interface LogListResult {
  logs: LogEntry[];
  total: number;
  limit: number;
  offset: number;
}

export async function fetchLogs(params: LogFilterParams = {}): Promise<LogListResult> {
  const searchParams = new URLSearchParams();
  if (params.query) searchParams.set('query', params.query);
  if (params.severity_max !== undefined && params.severity_max !== null) {
    searchParams.set('severity_max', params.severity_max.toString());
  }
  const sources: string[] = params.sources && params.sources.length > 0
    ? params.sources
    : (params.source ? (Array.isArray(params.source) ? params.source : [params.source]) : []);
  if (sources.length > 0) {
    searchParams.set('source', sources.join(','));
  }

  const apps: string[] = params.apps && params.apps.length > 0
    ? params.apps
    : (params.app_name ? (Array.isArray(params.app_name) ? params.app_name : [params.app_name]) : []);
  if (apps.length > 0) {
    searchParams.set('app_name', apps.join(','));
  }
  if (params.from) searchParams.set('from', params.from);
  if (params.to) searchParams.set('to', params.to);
  if (params.limit !== undefined) searchParams.set('limit', params.limit.toString());
  if (params.offset !== undefined) searchParams.set('offset', params.offset.toString());

  const qs = searchParams.toString();
  return apiFetch<LogListResult>(`/api/logs${qs ? `?${qs}` : ''}`);
}

export async function fetchLogContext(
  id: number,
  lines: number = 10,
  same_app: boolean = false,
): Promise<LogContextResponse> {
  const searchParams = new URLSearchParams({ lines: lines.toString() });
  if (same_app) {
    searchParams.set('same_app', 'true');
  }
  return apiFetch<LogContextResponse>(`/api/logs/${id}/context?${searchParams.toString()}`);
}

export async function fetchLogFacets(): Promise<import('../types.ts').LogFacetsResponse> {
  return apiFetch<import('../types.ts').LogFacetsResponse>('/api/logs/facets');
}

export async function deleteSingleLog(id: number): Promise<LogDeleteResponse> {
  return apiFetch<LogDeleteResponse>(`/api/logs/${id}`, {
    method: 'DELETE',
  });
}

export async function deleteLogs(params: LogDeleteParams): Promise<LogDeleteResponse> {
  return apiFetch<LogDeleteResponse>('/api/logs/delete', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function previewDeleteLogs(params: LogDeleteParams): Promise<LogDeletePreviewResponse> {
  return apiFetch<LogDeletePreviewResponse>('/api/logs/delete/preview', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

