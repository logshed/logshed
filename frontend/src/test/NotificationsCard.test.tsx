import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NotificationsCard } from '../components/settings/NotificationsCard.tsx';
import * as notifApi from '../api/notifications.ts';
import * as settingsApi from '../api/settings.ts';

vi.mock('../api/notifications.ts', () => ({
  fetchNotificationChannels: vi.fn(),
  createNotificationChannel: vi.fn(),
  updateNotificationChannel: vi.fn(),
  deleteNotificationChannel: vi.fn(),
  testNotificationTarget: vi.fn(),
  sendDailyDigest: vi.fn(),
}));

vi.mock('../api/settings.ts', () => ({
  fetchSettings: vi.fn(),
  updateSettings: vi.fn(),
}));

describe('NotificationsCard Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders empty state when no channels are configured', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([]);

    render(<NotificationsCard />);

    await waitFor(() => {
      expect(
        screen.getByText(/No notification targets configured/i)
      ).toBeInTheDocument();
    });
    expect(screen.getByText('0 targets')).toBeInTheDocument();
  });

  it('renders configured channels with masked URLs and allows toggling status', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([
      {
        id: 1,
        name: 'Homelab Discord',
        url: 'discord://1...9/a...f',
        is_enabled: true,
        created_at: '2026-09-18T12:00:00Z',
        updated_at: '2026-09-18T12:00:00Z',
      },
    ]);
    vi.mocked(notifApi.updateNotificationChannel).mockResolvedValueOnce({
      id: 1,
      name: 'Homelab Discord',
      url: 'discord://1...9/a...f',
      is_enabled: false,
      created_at: '2026-09-18T12:00:00Z',
      updated_at: '2026-09-18T12:05:00Z',
    });

    render(<NotificationsCard />);

    await waitFor(() => {
      expect(screen.getByText('Homelab Discord')).toBeInTheDocument();
      expect(screen.getByText('discord://1...9/a...f')).toBeInTheDocument();
      expect(screen.getByText('Active')).toBeInTheDocument();
    });

    // Click toggle status
    const statusBtn = screen.getByText('Active');
    fireEvent.click(statusBtn);

    await waitFor(() => {
      expect(notifApi.updateNotificationChannel).toHaveBeenCalledWith(1, { is_enabled: false });
      expect(screen.getByText('Disabled')).toBeInTheDocument();
    });
  });

  it('opens add modal, runs test notification, and creates channel', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([]);
    vi.mocked(notifApi.testNotificationTarget).mockResolvedValueOnce({
      success: true,
      message: 'Notification sent successfully.',
    });
    vi.mocked(notifApi.createNotificationChannel).mockResolvedValueOnce({
      id: 2,
      name: 'Ops Telegram',
      url: 'tgram://1...9/b...z',
      is_enabled: true,
      created_at: '2026-09-18T12:00:00Z',
      updated_at: '2026-09-18T12:00:00Z',
    });

    render(<NotificationsCard />);

    await waitFor(() => {
      expect(screen.getByText('New Target')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Target'));

    expect(screen.getByText('Add Notification Target')).toBeInTheDocument();

    const nameInput = screen.getByPlaceholderText(/e.g. Homelab Discord/i);
    const urlInput = screen.getByPlaceholderText(/discord:\/\/webhook_id/i);

    fireEvent.change(nameInput, { target: { value: 'Ops Telegram' } });
    fireEvent.change(urlInput, { target: { value: 'tgram://12345/bot_token' } });

    // Click Send Test Notification
    const testBtn = screen.getByRole('button', { name: /Send Test Notification/i });
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(notifApi.testNotificationTarget).toHaveBeenCalledWith({ url: 'tgram://12345/bot_token' });
      expect(screen.getByText('Notification sent successfully.')).toBeInTheDocument();
    });

    // Submit modal
    const submitBtn = screen.getByRole('button', { name: /Create Target/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(notifApi.createNotificationChannel).toHaveBeenCalledWith({
        name: 'Ops Telegram',
        url: 'tgram://12345/bot_token',
        is_enabled: true,
      });
      expect(screen.getByText('Ops Telegram')).toBeInTheDocument();
    });
  });

  it('disables daily digest controls when no active notification targets exist', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([
      {
        id: 1,
        name: 'Disabled Target',
        url: 'discord://1...9/a...f',
        is_enabled: false,
        created_at: '2026-09-18T12:00:00Z',
        updated_at: '2026-09-18T12:00:00Z',
      },
    ]);
    vi.mocked(settingsApi.fetchSettings).mockResolvedValueOnce({
      daily_digest_enabled: false,
      daily_digest_channel_id: null,
    } as any);

    render(<NotificationsCard />);

    await waitFor(() => {
      expect(screen.getByText('Daily Digest Rollup')).toBeInTheDocument();
    });

    const checkbox = screen.getByLabelText(/Enable 24-hour daily digest rollup/i);
    expect(checkbox).toBeDisabled();
    expect(
      screen.getByText(/Requires at least one active notification target configured above/i)
    ).toBeInTheDocument();
    expect(screen.queryByText('Send Digest Now')).not.toBeInTheDocument();
  });

  it('allows toggling daily digest, selecting channel, changing schedule time, and dispatching digest', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValue([
      {
        id: 1,
        name: 'Homelab Discord',
        url: 'discord://1...9/a...f',
        is_enabled: true,
        created_at: '2026-09-18T12:00:00Z',
        updated_at: '2026-09-18T12:00:00Z',
      },
    ]);
    let currentSettings: any = {
      daily_digest_enabled: false,
      daily_digest_channel_id: null,
    };
    vi.mocked(settingsApi.fetchSettings).mockImplementation(async () => ({ ...currentSettings }));
    vi.mocked(settingsApi.updateSettings).mockImplementation(async (updates) => {
      currentSettings = { ...currentSettings, ...updates };
      return { ...currentSettings };
    });
    vi.mocked(notifApi.sendDailyDigest).mockResolvedValueOnce({
      status: 'ok',
      total_logs: 1250,
      error_count: 5,
      top_errors: [],
      top_services: [],
      storage_delta: '+12 KB',
      channel_id: 1,
      notification_sent: true,
      triggered_at: '2026-09-28T12:00:00Z',
    });

    render(<NotificationsCard />);

    await waitFor(() => {
      expect(screen.getByText('Daily Digest Rollup')).toBeInTheDocument();
      expect(screen.getByText('Send Digest Now')).toBeInTheDocument();
    });

    // Toggle daily digest enable
    const digestCheckbox = screen.getByLabelText(/Enable 24-hour daily digest rollup/i);
    expect(digestCheckbox).not.toBeDisabled();
    fireEvent.click(digestCheckbox);

    await waitFor(() => {
      expect(settingsApi.updateSettings).toHaveBeenCalledWith({ daily_digest_enabled: true });
    });

    // Select channel
    const channelSelect = screen.getByLabelText(/Target Channel/i);
    fireEvent.change(channelSelect, { target: { value: '1' } });

    await waitFor(() => {
      expect(settingsApi.updateSettings).toHaveBeenCalledWith({ daily_digest_channel_id: 1 });
    });

    // Change schedule time - wait for any pending saves to complete, then interact
    const timeInput = screen.getByLabelText(/Schedule Time/i);
    // Wait until the input is enabled and not saving (previous operations have settled)
    await waitFor(() => {
      expect(timeInput).not.toBeDisabled();
    });
    // Now change and blur - the onBlur handler will save when the value differs from server value
    await act(async () => {
      fireEvent.change(timeInput, { target: { value: '14:30' } });
    });
    await act(async () => {
      fireEvent.blur(timeInput);
    });

    await waitFor(() => {
      expect(settingsApi.updateSettings).toHaveBeenCalledWith({ daily_digest_schedule_time: '14:30' });
    });

    // Click Send Digest Now
    const sendBtn = screen.getByText('Send Digest Now');
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(notifApi.sendDailyDigest).toHaveBeenCalled();
      expect(
        screen.getByText(/Daily digest dispatched successfully to Homelab Discord \(1,250 logs analyzed\)\./i)
      ).toBeInTheDocument();
    });
  });
});
