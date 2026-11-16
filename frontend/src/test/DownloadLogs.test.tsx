// Downloading logs: the API client, the Download Logs dialog, and its buttons in the Console.
import { screen, fireEvent, waitFor, within, render } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import { DownloadLogsDialog } from '../components/logs/DownloadLogsDialog.tsx';
import * as logsApi from '../api/logs.ts';
import { ApiError } from '../api/client.ts';
import { stubDownloads } from './downloadStubs.ts';
import { renderWithContext, setupConsoleMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

function exportResponse(body: string, headers: Record<string, string> = {}) {
  return new Response(body, {
    status: 200,
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': 'attachment; filename="logshed-logs-20261115-100000.csv"',
      'X-LogShed-Export-Rows': '2',
      'X-LogShed-Export-Capped': 'false',
      ...headers,
    },
  });
}

describe('downloadLogs', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends the search filters and reads the file name, row count and cap', async () => {
    const fetchMock = vi.fn().mockResolvedValue(exportResponse('id\r\n1\r\n', { 'X-LogShed-Export-Capped': 'true', 'X-LogShed-Export-Rows': '100000' }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await logsApi.downloadLogs({
      format: 'csv',
      maskSecrets: true,
      filters: { query: 'failed', source: ['nas', 'router'], app_name: 'smartd', severity_max: 3, from: '24h', to: '' },
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/logs/export');
    expect(init.method).toBe('POST');
    expect(new Headers(init.headers).get('X-Requested-With')).toBe('XMLHttpRequest');
    expect(JSON.parse(init.body)).toEqual({
      format: 'csv',
      mask_secrets: true,
      query: 'failed',
      sources: ['nas', 'router'],
      apps: ['smartd'],
      severity_max: 3,
      from: '24h',
    });
    expect(result.filename).toBe('logshed-logs-20261115-100000.csv');
    expect(result.rows).toBe(100000);
    expect(result.capped).toBe(true);
    expect(await result.blob.text()).toBe('id\r\n1\r\n');
  });

  it('sends ticked rows alone, without the filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(exportResponse(''));
    vi.stubGlobal('fetch', fetchMock);
    await logsApi.downloadLogs({ format: 'ndjson', maskSecrets: false, logIds: [3, 1], filters: { query: 'x' } });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ format: 'ndjson', mask_secrets: false, log_ids: [3, 1] });
  });

  it('turns an error status into an ApiError with the server message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Search took too long' }), { status: 504 })));
    await expect(logsApi.downloadLogs({ format: 'csv', maskSecrets: false })).rejects.toMatchObject({
      status: 504,
      message: 'Search took too long',
    });
  });
});

describe('Download Logs dialog', () => {
  let restoreDownloads: () => void;
  beforeEach(() => {
    vi.restoreAllMocks();
    restoreDownloads = stubDownloads();
  });
  afterEach(() => restoreDownloads());

  const blob = new Blob(['x']);
  const renderDialog = (props: Partial<React.ComponentProps<typeof DownloadLogsDialog>> = {}) => {
    const onClose = vi.fn();
    render(
      <DownloadLogsDialog isOpen onClose={onClose} filters={{ query: 'disk' }} selectedIds={[]} initialScope="search" {...props} />,
    );
    return { onClose };
  };

  it('downloads the search as CSV, exact, and closes', async () => {
    const download = vi.spyOn(logsApi, 'downloadLogs').mockResolvedValue({ blob, filename: 'f.csv', rows: 2, capped: false });
    const { onClose } = renderDialog();
    expect(screen.queryByText('Logs')).toBeNull(); // nothing ticked: no choice of logs
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(download).toHaveBeenCalledWith({ format: 'csv', maskSecrets: false, filters: { query: 'disk' } }, expect.any(AbortSignal));
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
  });

  it('downloads NDJSON with secrets masked', async () => {
    const download = vi.spyOn(logsApi, 'downloadLogs').mockResolvedValue({ blob, filename: 'f.ndjson', rows: 2, capped: false });
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: /^Format/ }));
    fireEvent.click(screen.getByRole('option', { name: /NDJSON/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Mask secrets' }));
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(download).toHaveBeenCalled());
    expect(download.mock.calls[0][0]).toMatchObject({ format: 'ndjson', maskSecrets: true });
  });

  it('says when the cap cut the search, and stays open', async () => {
    vi.spyOn(logsApi, 'downloadLogs').mockResolvedValue({ blob, filename: 'f.csv', rows: 100000, capped: true });
    const { onClose } = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    const note = await screen.findByRole('status');
    expect(note).toHaveTextContent('matched more than 100,000 logs, so the file holds the newest 100,000');
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('shows the server message on an error, and a cut-off download as stopped', async () => {
    const download = vi.spyOn(logsApi, 'downloadLogs').mockRejectedValueOnce(new ApiError(504, 'Search took too long'));
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Search took too long');
    download.mockRejectedValueOnce(new TypeError('network error'));
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('stopped before the end'));
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('offers the ticked rows first when opened for them, or the search', async () => {
    const download = vi.spyOn(logsApi, 'downloadLogs').mockResolvedValue({ blob, filename: 'f.csv', rows: 2, capped: false });
    renderDialog({ selectedIds: [7, 9], initialScope: 'selected' });
    expect(screen.getByRole('button', { name: /^Logs/ })).toHaveTextContent('Ticked rows (2)');
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(download).toHaveBeenCalled());
    expect(download.mock.calls[0][0]).toEqual({ format: 'csv', maskSecrets: false, logIds: [7, 9] });
  });

  it('Cancel during a download stops it and saves nothing', async () => {
    let signal: AbortSignal | undefined;
    vi.spyOn(logsApi, 'downloadLogs').mockImplementation((_req, s) => {
      signal = s;
      return new Promise((_, reject) => s?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))));
    });
    const { onClose } = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(await screen.findByRole('button', { name: 'Preparing...' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(signal?.aborted).toBe(true);
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});

describe('Download in the Console', () => {
  setupConsoleMocks();

  it('the toolbar button downloads the current search', async () => {
    const download = vi.spyOn(logsApi, 'downloadLogs').mockResolvedValue({ blob: new Blob(['x']), filename: 'f.csv', rows: 1, capped: false });
    const restore = stubDownloads();
    try {
      window.history.replaceState(null, '', '/?q=timeout');
      renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
      fireEvent.click(await screen.findByRole('button', { name: 'Download logs of this search' }));
      const dialog = await screen.findByRole('dialog', { name: 'Download Logs' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Download' }));
      await waitFor(() => expect(download).toHaveBeenCalled());
      expect(download.mock.calls[0][0]).toMatchObject({ filters: expect.objectContaining({ query: 'timeout' }) });
      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Download Logs' })).toBeNull());
    } finally {
      restore();
    }
  });

  it('the selection bar downloads the ticked rows', async () => {
    const download = vi.spyOn(logsApi, 'downloadLogs').mockResolvedValue({ blob: new Blob(['x']), filename: 'f.csv', rows: 1, capped: false });
    const restore = stubDownloads();
    try {
      renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
      await screen.findByText('Nginx upstream connection timeout');
      fireEvent.click(document.querySelectorAll('.log-row')[0].firstElementChild as HTMLElement);
      fireEvent.click(await screen.findByRole('button', { name: 'Download (1)' }));
      const dialog = await screen.findByRole('dialog', { name: 'Download Logs' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Download' }));
      await waitFor(() => expect(download).toHaveBeenCalled());
      expect(download.mock.calls[0][0].logIds).toHaveLength(1);
    } finally {
      restore();
    }
  });
});
