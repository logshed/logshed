import { apiFetch } from './client.ts';
import { SystemSettings } from '../types.ts';

export interface SettingsResponseData extends SystemSettings {
  has_ai_api_key: boolean;
  server_timezone?: string;
  server_tz_name?: string;
  server_tz_offset_minutes?: number;
}

let cachedSettings: SettingsResponseData | null = null;
let settingsPromise: Promise<SettingsResponseData> | null = null;

export function getCachedServerTimezone(): string | undefined {
  return cachedSettings?.server_timezone || undefined;
}

export function getCachedServerTzName(): string | undefined {
  return cachedSettings?.server_tz_name || undefined;
}

export function getCachedServerTzOffset(): number | undefined {
  return cachedSettings?.server_tz_offset_minutes ?? undefined;
}

export async function fetchSettings(forceRefresh = false): Promise<SettingsResponseData> {
  if (!forceRefresh && cachedSettings) {
    return cachedSettings;
  }
  if (!forceRefresh && settingsPromise) {
    return settingsPromise;
  }
  settingsPromise = apiFetch<SettingsResponseData>('/api/settings').then((data) => {
    cachedSettings = data;
    settingsPromise = null;
    return data;
  }).catch((err) => {
    settingsPromise = null;
    throw err;
  });
  return settingsPromise;
}

export async function updateSettings(settings: Partial<SystemSettings>): Promise<{ status: string }> {
  cachedSettings = null;
  return apiFetch<{ status: string }>('/api/settings', {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}
