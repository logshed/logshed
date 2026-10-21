import { apiFetch } from './client.ts';
import { SystemSettings } from '../types.ts';

export interface SettingsResponseData extends SystemSettings {
  has_ai_api_key: boolean;
  server_timezone?: string | null;
  server_tz_name?: string;
  server_tz_offset_minutes?: number;
}

let cachedSettings: SettingsResponseData | null = null;
let settingsPromise: Promise<SettingsResponseData> | null = null;

export function getCachedServerTimezone(): string | undefined {
  return cachedSettings?.server_timezone || undefined;
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

export interface UpdateSettingsResponse {
  status: string;
  /** True when a stored AI API key was dropped because its provider's base URL changed. */
  ai_api_key_cleared?: boolean;
  ai_api_key_cleared_providers?: string[];
}

export async function updateSettings(settings: Partial<SystemSettings>): Promise<UpdateSettingsResponse> {
  cachedSettings = null;
  return apiFetch<UpdateSettingsResponse>('/api/settings', {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}
