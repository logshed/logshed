import { apiFetch } from './client.ts';
import {
  NotificationChannel,
  NotificationChannelCreate,
  NotificationChannelUpdate,
  NotificationTestRequest,
  NotificationTestResponse,
  DailyDigestRunResponse,
} from '../types.ts';

export async function fetchNotificationChannels(): Promise<NotificationChannel[]> {
  return apiFetch<NotificationChannel[]>('/api/notifications/channels');
}

export async function createNotificationChannel(
  data: NotificationChannelCreate
): Promise<NotificationChannel> {
  return apiFetch<NotificationChannel>('/api/notifications/channels', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateNotificationChannel(
  id: number,
  data: NotificationChannelUpdate
): Promise<NotificationChannel> {
  return apiFetch<NotificationChannel>('/api/notifications/channels/' + id, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteNotificationChannel(id: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/api/notifications/channels/' + id, {
    method: 'DELETE',
  });
}

export async function testNotificationTarget(
  data: NotificationTestRequest
): Promise<NotificationTestResponse> {
  return apiFetch<NotificationTestResponse>('/api/notifications/test', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function sendDailyDigest(): Promise<DailyDigestRunResponse> {
  return apiFetch<DailyDigestRunResponse>('/api/notifications/digest/send', {
    method: 'POST',
  });
}
