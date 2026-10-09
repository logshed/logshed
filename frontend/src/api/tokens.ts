import { apiFetch } from './client.ts';
import { ApiToken, ApiTokenCreateRequest, ApiTokenCreateResponse } from '../types.ts';

export async function fetchApiTokens(): Promise<ApiToken[]> {
  return apiFetch<ApiToken[]>('/api/tokens');
}

export async function createApiToken(data: ApiTokenCreateRequest): Promise<ApiTokenCreateResponse> {
  return apiFetch<ApiTokenCreateResponse>('/api/tokens', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function revokeApiToken(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/tokens/' + id, {
    method: 'DELETE',
  });
}
