import { apiFetch, apiFetchBlob } from './client.ts';

/** The parts of a configuration file, in the order an import applies them. */
export type ConfigSection =
  | 'notification_targets'
  | 'docker_hosts'
  | 'host_aliases'
  | 'alert_rules'
  | 'drop_rules'
  | 'saved_views'
  | 'maintenance_schedules'
  | 'api_tokens'
  | 'settings';

export interface ConfigPlanItem {
  label: string;
  action: 'add' | 'replace' | 'unchanged' | 'skip';
  note?: string | null;
}

export interface ConfigSectionPreview {
  name: ConfigSection;
  add: number;
  replace: number;
  unchanged: number;
  skip: number;
  items: ConfigPlanItem[];
  notes: string[];
}

export interface ConfigImportPreview {
  has_secrets: boolean;
  secrets_read: boolean;
  logshed_version?: string | null;
  exported_at?: string | null;
  sections: ConfigSectionPreview[];
}

export interface ConfigSectionResult {
  name: ConfigSection;
  added: number;
  replaced: number;
  unchanged: number;
  skipped: number;
  failed: { label: string; error: string }[];
  notes: string[];
}

/**
 * The whole configuration as a JSON file. With includeSecrets they are encrypted with the password,
 * and the server first checks the admin password.
 */
export async function exportConfiguration(includeSecrets: boolean, password?: string, adminPassword?: string): Promise<Blob> {
  return apiFetchBlob('/api/config/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      include_secrets: includeSecrets,
      password: includeSecrets ? password : null,
      admin_password: includeSecrets ? adminPassword : null,
    }),
  });
}

/** What importing the file would do, per section. No password reads the file without its secrets. */
export async function previewConfigurationImport(file: object, password?: string): Promise<ConfigImportPreview> {
  return apiFetch<ConfigImportPreview>('/api/config/import/preview', {
    method: 'POST',
    body: JSON.stringify({ file, password: password || null }),
  });
}

export async function importConfiguration(
  file: object,
  sections: ConfigSection[],
  password?: string,
): Promise<{ sections: ConfigSectionResult[] }> {
  return apiFetch<{ sections: ConfigSectionResult[] }>('/api/config/import', {
    method: 'POST',
    body: JSON.stringify({ file, sections, password: password || null }),
  });
}
