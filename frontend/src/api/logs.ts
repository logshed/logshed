import { apiFetch, toApiError } from './client.ts';
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
  total_capped?: boolean;
}

/** One bar of the volume histogram: its span (UTC ISO times) and its logs by severity group. */
export interface LogHistogramBucket {
  start: string;
  end: string;
  error: number;
  warning: number;
  info: number;
  debug: number;
}

export interface LogHistogram {
  range_from: string;
  range_to: string;
  bucket_seconds: number;
  buckets: LogHistogramBucket[];
}

export async function fetchLogs(
  params: LogFilterParams = {},
  options: { signal?: AbortSignal } = {},
): Promise<LogListResult> {
  const searchParams = filterSearchParams(params);
  if (params.limit !== undefined) searchParams.set('limit', params.limit.toString());
  if (params.offset !== undefined) searchParams.set('offset', params.offset.toString());

  const qs = searchParams.toString();
  return apiFetch<LogListResult>(`/api/logs${qs ? `?${qs}` : ''}`, options.signal ? { signal: options.signal } : {});
}

/**
 * Log counts per time bucket by severity group, for the Console's volume histogram, over the
 * filters' range (the last hour when they have no From).
 */
export async function fetchLogHistogram(
  params: LogFilterParams = {},
  options: { signal?: AbortSignal } = {},
): Promise<LogHistogram> {
  const qs = filterSearchParams(params).toString();
  return apiFetch<LogHistogram>(`/api/logs/histogram${qs ? `?${qs}` : ''}`, options.signal ? { signal: options.signal } : {});
}

/** The query string of a Console filter: search, severity, hosts, apps and time bounds. */
function filterSearchParams(params: LogFilterParams): URLSearchParams {
  const searchParams = new URLSearchParams();
  if (params.query) searchParams.set('query', params.query);
  if (params.severity_max !== undefined && params.severity_max !== null) {
    searchParams.set('severity_max', params.severity_max.toString());
  }
  const { sources, apps } = filterLists(params);
  if (sources.length > 0) {
    searchParams.set('source', sources.join(','));
  }
  if (apps.length > 0) {
    searchParams.set('app_name', apps.join(','));
  }
  if (params.from) searchParams.set('from', params.from);
  if (params.to) searchParams.set('to', params.to);
  return searchParams;
}

/** The host and app lists of a filter, from its list or its single-value field. */
function filterLists(params: LogFilterParams): { sources: string[]; apps: string[] } {
  const sources: string[] = params.sources && params.sources.length > 0
    ? params.sources
    : (params.source ? (Array.isArray(params.source) ? params.source : [params.source]) : []);
  const apps: string[] = params.apps && params.apps.length > 0
    ? params.apps
    : (params.app_name ? (Array.isArray(params.app_name) ? params.app_name : [params.app_name]) : []);
  return { sources, apps };
}

export type LogDownloadFormat = 'csv' | 'ndjson';

/** Most logs one download holds (the newest); the server says when a search matched more. */
export const LOG_DOWNLOAD_CAP = 100_000;

export interface LogDownloadRequest {
  format: LogDownloadFormat;
  maskSecrets: boolean;
  /** The ticked rows, downloaded alone; without them, the logs the filters select. */
  logIds?: number[];
  filters?: LogFilterParams;
}

export interface LogDownload {
  blob: Blob;
  filename: string;
  /** How many logs the file holds, as the server counted them when it started. */
  rows: number;
  /** True when the search matched more than LOG_DOWNLOAD_CAP logs and only the newest are in the file. */
  capped: boolean;
}

/** Downloads logs as CSV or NDJSON. Aborting the signal stops the server's search too. */
export async function downloadLogs(request: LogDownloadRequest, signal?: AbortSignal): Promise<LogDownload> {
  const body: Record<string, unknown> = { format: request.format, mask_secrets: request.maskSecrets };
  if (request.logIds && request.logIds.length > 0) {
    body.log_ids = request.logIds;
  } else if (request.filters) {
    const f = request.filters;
    const { sources, apps } = filterLists(f);
    if (f.query) body.query = f.query;
    if (sources.length > 0) body.sources = sources;
    if (apps.length > 0) body.apps = apps;
    if (f.severity_max !== undefined && f.severity_max !== null) body.severity_max = f.severity_max;
    if (f.from) body.from = f.from;
    if (f.to) body.to = f.to;
  }
  const endpoint = '/api/logs/export';
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await toApiError(response, endpoint);
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `logshed-logs.${request.format}`;
  return {
    // A download cut off part way rejects here, so a short file is never saved as if it were whole
    blob: await response.blob(),
    filename,
    rows: Number(response.headers.get('X-LogShed-Export-Rows') ?? 0),
    capped: response.headers.get('X-LogShed-Export-Capped') === 'true',
  };
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

