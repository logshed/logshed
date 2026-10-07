import { apiFetch } from './client.ts';
import {
  SavedView,
  SavedViewCreate,
  SavedViewUpdate,
} from '../types.ts';

export async function fetchSavedViews(): Promise<SavedView[]> {
  return apiFetch<SavedView[]>('/api/saved-views');
}

export async function createSavedView(data: SavedViewCreate): Promise<SavedView> {
  return apiFetch<SavedView>('/api/saved-views', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateSavedView(
  id: number,
  data: SavedViewUpdate
): Promise<SavedView> {
  return apiFetch<SavedView>(`/api/saved-views/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteSavedView(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/saved-views/${id}`, {
    method: 'DELETE',
  });
}
