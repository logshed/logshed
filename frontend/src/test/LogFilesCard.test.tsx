import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as settingsApi from '../api/settings.ts';
import { LOG_FILES_SHOWN, LogFilesCard } from '../components/storage/LogFilesCard.tsx';
import { dbSplitLabel } from '../components/storage/StorageCard.tsx';
import { LogFileItem, StorageMetricsResponse } from '../types.ts';

function dayFile(day: string, state = 'sealed', logs = 1000): LogFileItem {
  return {
    day,
    file_name: `logs-${day}-UTC.db`,
    state,
    logs,
    size_bytes: 2 * 1024 * 1024,
    expires_at: new Date(Date.parse(`${day}T00:00:00Z`) + 15 * 86400_000).toISOString(),
  };
}

const LEGACY: LogFileItem = {
  day: 'legacy',
  file_name: 'logs.db',
  state: 'legacy',
  logs: 1204551,
  size_bytes: null,
  expires_at: '2026-11-26T09:15:00+00:00',
};

function days(count: number): LogFileItem[] {
  const day = (i: number) => new Date(Date.parse('2026-11-13T00:00:00Z') - i * 86400_000).toISOString().slice(0, 10);
  return Array.from({ length: count }, (_v, i) => dayFile(day(i), i === 0 ? 'open' : 'sealed'));
}

describe('LogFilesCard', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // The server's zone: London is on GMT in November, BST in summer
    vi.spyOn(settingsApi, 'fetchSettings').mockResolvedValue({ retention_days: 14, server_timezone: 'Europe/London' } as any);
  });

  it('lists the newest days first with logs, size, state and expiry in server time', async () => {
    render(<LogFilesCard files={days(3)} retentionDays={14} />);
    const rows = within(screen.getByRole('table', { name: 'Log files' })).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].firstChild?.textContent)).toEqual(['2026-11-13', '2026-11-12', '2026-11-11']);
    expect(within(rows[0]).getByText('open')).toBeInTheDocument();
    expect(within(rows[1]).getByText('sealed')).toBeInTheDocument();
    expect(within(rows[0]).getByText('1,000')).toBeInTheDocument();
    expect(within(rows[0]).getByText('2 MB')).toBeInTheDocument();
    await waitFor(() => expect(within(rows[0]).getByText('2026-11-28 00:00')).toBeInTheDocument());
    expect(screen.getByText(/deleted a whole day at a time/)).toHaveTextContent('retention of 14 days');
  });

  it('shows the expiry in the server zone, not UTC, in summer', async () => {
    render(<LogFilesCard files={[dayFile('2026-07-01')]} />);
    // 00:00 UTC on 16 July is 01:00 in London (BST)
    await waitFor(() => expect(screen.getByText('2026-07-16 01:00')).toBeInTheDocument());
  });

  it('shows seven days and the rest on request', () => {
    render(<LogFilesCard files={days(15)} />);
    const table = screen.getByRole('table', { name: 'Log files' });
    expect(within(table).getAllByRole('row')).toHaveLength(LOG_FILES_SHOWN + 1);
    fireEvent.click(screen.getByRole('button', { name: 'Show all 15' }));
    expect(within(table).getAllByRole('row')).toHaveLength(16);
    fireEvent.click(screen.getByRole('button', { name: 'Show fewer' }));
    expect(within(table).getAllByRole('row')).toHaveLength(LOG_FILES_SHOWN + 1);
  });

  it('has no button when every day fits', () => {
    render(<LogFilesCard files={days(LOG_FILES_SHOWN)} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('ends with the logs from before 1.4.0 while any are left, always shown', async () => {
    render(<LogFilesCard files={[...days(10), LEGACY]} />);
    const row = screen.getByTestId('legacy-row');
    expect(row).toHaveTextContent('Before 1.4.0');
    expect(row).toHaveTextContent('1,204,551');
    expect(row).toHaveTextContent('in logs.db');
    await waitFor(() => expect(row).toHaveTextContent('all gone by 2026-11-26 09:15'));
    expect(screen.getByRole('button', { name: 'Show all 10' })).toBeInTheDocument();
  });

  it('stacks each day on a phone, as the other tables do, with nothing cut off at the side', async () => {
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    render(<LogFilesCard files={[...days(10), LEGACY]} />);
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    const list = screen.getByRole('list', { name: 'Log files' });
    const items = within(list).getAllByRole('listitem');
    expect(items).toHaveLength(LOG_FILES_SHOWN + 1);
    expect(items[0]).toHaveTextContent('2026-11-13');
    expect(within(items[0]).getByText('open')).toBeInTheDocument();
    expect(items[0]).toHaveTextContent('Logs: 1,000');
    expect(items[0]).toHaveTextContent('Size: 2 MB');
    await waitFor(() => expect(items[0]).toHaveTextContent('Expires: 2026-11-28 00:00'));
    const legacy = screen.getByTestId('legacy-row');
    expect(legacy).toHaveTextContent('Before 1.4.0');
    expect(within(legacy).getByText('before 1.4.0')).toBeInTheDocument();
    expect(legacy).toHaveTextContent('Logs: 1,204,551');
    expect(legacy).toHaveTextContent('in logs.db');
    await waitFor(() => expect(legacy).toHaveTextContent('All gone by: 2026-11-26 09:15'));
    fireEvent.click(screen.getByRole('button', { name: 'Show all 10' }));
    expect(within(list).getAllByRole('listitem')).toHaveLength(11);
  });

  it('is not drawn without day files or legacy logs', () => {
    const { container } = render(<LogFilesCard files={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('dbSplitLabel', () => {
  const base: StorageMetricsResponse = { db_size_bytes: 0, disk_free_bytes: 0, disk_total_bytes: 0, history: [] };

  it('splits the database size into logs.db and the day files', () => {
    const metrics = { ...base, main_db_size_bytes: 18 * 1024 ** 2, day_files_size_bytes: 4 * 1024 ** 3, log_files: [...days(14), LEGACY] };
    expect(dbSplitLabel(metrics)).toBe('logs.db 18 MB + 14 day files 4 GB');
    expect(dbSplitLabel({ ...metrics, log_files: days(1) })).toBe('logs.db 18 MB + 1 day file 4 GB');
  });

  it('names the files as before when there are no day files yet', () => {
    expect(dbSplitLabel(base)).toBe('logs.db + WAL + SHM');
    expect(dbSplitLabel({ ...base, main_db_size_bytes: 1, log_files: [LEGACY] })).toBe('logs.db + WAL + SHM');
  });
});
