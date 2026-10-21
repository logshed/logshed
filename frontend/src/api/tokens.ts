import { apiFetch } from './client.ts';
import { ApiToken, ApiTokenCreateRequest, ApiTokenCreateResponse, ApiTokenUpdateRequest } from '../types.ts';

export async function fetchApiTokens(): Promise<ApiToken[]> {
  return apiFetch<ApiToken[]>('/api/tokens');
}

export async function createApiToken(data: ApiTokenCreateRequest): Promise<ApiTokenCreateResponse> {
  return apiFetch<ApiTokenCreateResponse>('/api/tokens', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/** Rename a token, change its scopes, or both; send only the fields that changed. */
export async function updateApiToken(id: number, data: ApiTokenUpdateRequest): Promise<ApiToken> {
  return apiFetch<ApiToken>('/api/tokens/' + id, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function revokeApiToken(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/tokens/' + id, {
    method: 'DELETE',
  });
}
