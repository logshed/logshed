import { useState } from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NotificationsCard } from '../components/settings/NotificationsCard.tsx';
import * as notifApi from '../api/notifications.ts';
import type { SystemSettings } from '../types.ts';
import { chooseOption, shownValue } from './selectDropdownHelpers.ts';

vi.mock('../api/notifications.ts', () => ({
  fetchNotificationChannels: vi.fn(),
  createNotificationChannel: vi.fn(),
  updateNotificationChannel: vi.fn(),
  deleteNotificationChannel: vi.fn(),
  testNotificationTarget: vi.fn(),
  sendDailyDigest: vi.fn(),
}));

/** The card as the settings page holds it: the digest fields live in the page's form state. */
function DigestHarness({
  settings = null,
  onSettingsSaved,
  onDigestChange,
}: {
  settings?: SystemSettings | null;
  onSettingsSaved?: () => void;
  onDigestChange?: (field: 'enabled' | 'channelId' | 'scheduleTime', value: unknown) => void;
}) {
  const [enabled, setEnabled] = useState(Boolean(settings?.daily_digest_enabled));
  const [channelId, setChannelId] = useState<number | null>(settings?.daily_digest_channel_id ?? null);
  const [scheduleTime, setScheduleTime] = useState(settings?.daily_digest_schedule_time || '09:00');
  return (
    <NotificationsCard
      settings={settings}
      onSettingsSaved={onSettingsSaved}
      digestEnabled={enabled}
      onDigestEnabledChange={(value) => {
        setEnabled(value);
        onDigestChange?.('enabled', value);
      }}
      digestChannelId={channelId}
      onDigestChannelIdChange={(value) => {
        setChannelId(value);
        onDigestChange?.('channelId', value);
      }}
      digestScheduleTime={scheduleTime}
      onDigestScheduleTimeChange={(value) => {
        setScheduleTime(value);
        onDigestChange?.('scheduleTime', value);
      }}
    />
  );
}

describe('NotificationsCard Component', () => {
  beforeEach(() => {
    // Also drops one-time replies a test set but never used, so one failure cannot spill into the next test
    vi.resetAllMocks();
  });

  it('labels the digest schedule time with the server time zone it runs in', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValue([]);
    // The runner is in UTC, so a browser-time label could not pass for this one
    const { unmount } = render(<DigestHarness settings={{ server_timezone: 'Asia/Kolkata' } as SystemSettings} />);
    expect(await screen.findByLabelText('Schedule Time (Asia/Kolkata)')).toBeInTheDocument();
    unmount();

    render(<DigestHarness settings={{ server_timezone: null } as SystemSettings} />);
    expect(await screen.findByLabelText('Schedule Time (server time)')).toBeInTheDocument();
  });

  it('renders empty state when no channels are configured', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([]);

    render(<DigestHarness />);

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

    render(<DigestHarness />);

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

    render(<DigestHarness />);

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

  it('renders Apprise documentation links pointing to services and url-builder', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValueOnce([]);

    render(<DigestHarness />);

    await waitFor(() => {
      expect(screen.getByText('New Target')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('New Target'));

    expect(screen.getByText('Add Notification Target')).toBeInTheDocument();

    const servicesLink = screen.getByRole('link', { name: /Services & Syntax/i });
    expect(servicesLink).toHaveAttribute('href', 'https://appriseit.com/services/');
    expect(servicesLink).toHaveAttribute('target', '_blank');

    const builderLink = screen.getByRole('link', { name: /URL Builder/i });
    expect(builderLink).toHaveAttribute('href', 'https://appriseit.com/url-builder/');
    expect(builderLink).toHaveAttribute('target', '_blank');
  });

  it('allows daily digest controls when no active notification targets exist (History only)', async () => {
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
    render(<DigestHarness settings={{ daily_digest_enabled: false, daily_digest_channel_id: null } as SystemSettings} />);

    await waitFor(() => {
      expect(screen.getByText('Daily Digest Rollup')).toBeInTheDocument();
    });

    const checkbox = screen.getByLabelText(/Enable 24-hour daily digest rollup/i);
    expect(checkbox).not.toBeDisabled();
    expect(
      screen.getByText(/No active notification targets configured\. Digests will still be recorded and viewable in History\./i)
    ).toBeInTheDocument();
    expect(screen.getByText('Send Digest Now')).toBeInTheDocument();
  });

  it('reports digest changes to the settings page and dispatches a digest', async () => {
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
    const onDigestChange = vi.fn();

    render(
      <DigestHarness
        settings={{ daily_digest_enabled: false, daily_digest_channel_id: null } as SystemSettings}
        onDigestChange={onDigestChange}
      />
    );

    await waitFor(() => {
      expect(screen.getByLabelText('Delete Homelab Discord')).toBeInTheDocument();
    });
    // The channel list and time stay shut while the digest is off
    expect(screen.getByLabelText(/Target Channel/i)).toBeDisabled();
    expect(screen.getByLabelText(/Schedule Time/i)).toBeDisabled();

    fireEvent.click(screen.getByLabelText(/Enable 24-hour daily digest rollup/i));
    expect(onDigestChange).toHaveBeenLastCalledWith('enabled', true);

    const channelSelect = screen.getByLabelText(/Target Channel/i);
    expect(channelSelect).not.toBeDisabled();
    chooseOption(channelSelect, 'Channel: Homelab Discord');
    expect(onDigestChange).toHaveBeenLastCalledWith('channelId', 1);

    const timeInput = screen.getByLabelText(/Schedule Time/i);
    fireEvent.change(timeInput, { target: { value: '25:00' } });
    expect(onDigestChange).toHaveBeenLastCalledWith('scheduleTime', '25:00');
    fireEvent.blur(timeInput);
    expect(screen.getByText('Enter a valid 24-hour time, e.g. 09:00 or 23:30.')).toBeInTheDocument();
    fireEvent.change(timeInput, { target: { value: '14:30' } });
    fireEvent.blur(timeInput);
    expect(screen.getByText('24-hour format (e.g. 09:00).')).toBeInTheDocument();
    expect(onDigestChange).toHaveBeenLastCalledWith('scheduleTime', '14:30');

    fireEvent.click(screen.getByRole('button', { name: 'Send Digest Now' }));
    await waitFor(() => {
      expect(notifApi.sendDailyDigest).toHaveBeenCalled();
      expect(
        screen.getByText(/Daily digest dispatched successfully to Homelab Discord and saved to History \(1,250 logs\)\./i)
      ).toBeInTheDocument();
    });
  });

  const discordChannel = {
    id: 1,
    name: 'Homelab Discord',
    url: 'discord://1...9/a...f',
    is_enabled: true,
    created_at: '2026-09-18T12:00:00Z',
    updated_at: '2026-09-18T12:00:00Z',
  };

  it('warns that the digest was saved but not delivered when notification_sent is false', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValue([discordChannel]);
    vi.mocked(notifApi.sendDailyDigest).mockResolvedValueOnce({
      status: 'ok',
      total_logs: 10,
      error_count: 0,
      top_errors: [],
      top_services: [],
      storage_delta: '+1 KB',
      channel_id: 1,
      notification_sent: false,
      triggered_at: '2026-09-28T12:00:00Z',
    });

    render(<DigestHarness settings={{ daily_digest_enabled: true, daily_digest_channel_id: 1 } as SystemSettings} />);
    // Wait for the channels: the message names the channel only once the list has loaded
    await waitFor(() => {
      expect(shownValue(screen.getByLabelText(/Target Channel/i))).toBe('Channel: Homelab Discord');
    });
    fireEvent.click(screen.getByText('Send Digest Now'));

    await waitFor(() => {
      expect(
        screen.getByText(/Digest saved to History, but delivery to Homelab Discord failed/i)
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/dispatched successfully/i)).not.toBeInTheDocument();
  });

  it('asks the settings page to reload after the digest channel is deleted', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValue([discordChannel]);
    vi.mocked(notifApi.deleteNotificationChannel).mockResolvedValue({ status: 'ok' } as any);
    const onSettingsSaved = vi.fn();

    render(
      <DigestHarness
        settings={{ daily_digest_enabled: true, daily_digest_channel_id: 1 } as SystemSettings}
        onSettingsSaved={onSettingsSaved}
      />
    );
    await waitFor(() => {
      expect(shownValue(screen.getByLabelText(/Target Channel/i))).toBe('Channel: Homelab Discord');
    });

    fireEvent.click(screen.getByLabelText('Delete Homelab Discord'));
    fireEvent.click(screen.getByText('Delete Target'));

    // The page reloads its settings, which the server has already cleared of the deleted channel
    await waitFor(() => {
      expect(onSettingsSaved).toHaveBeenCalledTimes(1);
    });
  });

  it('sends only one delete request when the delete button is clicked twice', async () => {
    vi.mocked(notifApi.fetchNotificationChannels).mockResolvedValue([discordChannel]);
    let resolveDelete: (v: any) => void = () => {};
    vi.mocked(notifApi.deleteNotificationChannel).mockImplementation(
      () => new Promise((resolve) => { resolveDelete = resolve; })
    );

    render(<DigestHarness />);
    await waitFor(() => {
      expect(screen.getByLabelText('Delete Homelab Discord')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText('Delete Homelab Discord'));
    const confirm = screen.getByText('Delete Target');
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(notifApi.deleteNotificationChannel).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveDelete({ status: 'ok' });
    });
  });
});

describe('NotificationsCard action buttons', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('gives the row icon buttons the touch-target pattern with a gap between them', async () => {
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
    render(<DigestHarness />);
    const edit = await screen.findByRole('button', { name: 'Edit Homelab Discord' });
    for (const name of ['Test Homelab Discord', 'Edit Homelab Discord', 'Delete Homelab Discord']) {
      const btn = screen.getByRole('button', { name });
      expect(btn.className).toContain('w-8 h-8 sm:w-auto sm:h-auto sm:p-1');
    }
    expect(edit.parentElement!.className).toContain('gap-1.5');
  });
});
