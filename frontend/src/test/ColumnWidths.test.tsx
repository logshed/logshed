// Resizable Console columns: the per-browser storage helper, the width limits and fitting, the
// drag handle (pointer and keyboard), and the Console header with its Reset widths button.
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readBrowserPref, removeBrowserPref, writeBrowserPref } from '../utils/browserPrefs.ts';
import {
  DEFAULT_COLUMN_WIDTHS,
  MAX_COLUMN_WIDTH,
  MIN_COLUMN_WIDTHS,
  MIN_MESSAGE_WIDTH,
  fitColumnWidths,
  maxColumnWidth,
  parseStoredWidths,
} from '../components/logs/useColumnWidths.ts';
import { ColumnResizeHandle } from '../components/logs/ColumnResizeHandle.tsx';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import { renderWithContext, setupConsoleMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

const KEY = 'logshed.console.columnWidths.v1';

describe('browserPrefs', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('writes, reads and removes a value under the logshed prefix', () => {
    writeBrowserPref('x.v1', { a: 1 });
    expect(localStorage.getItem('logshed.x.v1')).toBe('{"a":1}');
    expect(readBrowserPref('x.v1', (v) => v as { a: number })).toEqual({ a: 1 });
    removeBrowserPref('x.v1');
    expect(readBrowserPref('x.v1', (v) => v)).toBeNull();
  });

  it('treats unreadable JSON and values its check refuses as not set', () => {
    localStorage.setItem('logshed.x.v1', '{not json');
    expect(readBrowserPref('x.v1', (v) => v)).toBeNull();
    localStorage.setItem('logshed.x.v1', '"text"');
    expect(readBrowserPref('x.v1', (v) => (typeof v === 'number' ? v : null))).toBeNull();
  });

  it('does nothing and throws nothing when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    expect(readBrowserPref('x.v1', (v) => v)).toBeNull();
    expect(() => writeBrowserPref('x.v1', 1)).not.toThrow();
    expect(() => removeBrowserPref('x.v1')).not.toThrow();
  });
});

describe('column width limits', () => {
  it('keeps stored widths that are whole numbers within their limits, and drops the rest', () => {
    expect(parseStoredWidths({ time: 180.4, host: 10, app: 'wide' })).toEqual({ time: 180 });
    expect(parseStoredWidths({ time: MAX_COLUMN_WIDTH + 1, host: Infinity })).toBeNull();
    expect(parseStoredWidths(null)).toBeNull();
    expect(parseStoredWidths([200, 200])).toBeNull();
  });

  it('fits the columns to a narrow header, narrowing app, then host, then time, to their minimums', () => {
    const wanted = { time: 300, host: 300, app: 300 };
    // 1280: room for 1280 - 127 fixed - 60 actions - 200 message = 893 px
    expect(fitColumnWidths(wanted, 1280, true)).toEqual({ time: 300, host: 300, app: 293 });
    // 800: room for 413 px; app and host go to their minimums before time gives anything
    expect(fitColumnWidths(wanted, 800, true)).toEqual({ time: 413 - MIN_COLUMN_WIDTHS.host - MIN_COLUMN_WIDTHS.app, host: MIN_COLUMN_WIDTHS.host, app: MIN_COLUMN_WIDTHS.app });
    // Never below the minimums, even when the message column then gets less
    expect(fitColumnWidths(wanted, 300, false)).toEqual(MIN_COLUMN_WIDTHS);
    // Not measured yet: only the limits apply
    expect(fitColumnWidths({ time: 10, host: 5000, app: 140 }, 0, true)).toEqual({ time: MIN_COLUMN_WIDTHS.time, host: MAX_COLUMN_WIDTH, app: 140 });
  });

  it('gives the widest a column can be with the others as they are', () => {
    const widths = { ...DEFAULT_COLUMN_WIDTHS };
    expect(maxColumnWidth('app', widths, 1280, false)).toBe(1280 - 127 - MIN_MESSAGE_WIDTH - 120 - 130);
    expect(maxColumnWidth('app', widths, 1280, true)).toBe(1280 - 127 - 60 - MIN_MESSAGE_WIDTH - 120 - 130);
    expect(maxColumnWidth('time', widths, 400, true)).toBe(MIN_COLUMN_WIDTHS.time);
    expect(maxColumnWidth('time', widths, 0, true)).toBe(MAX_COLUMN_WIDTH);
  });
});

describe('ColumnResizeHandle', () => {
  const setup = () => {
    const props = {
      label: 'Host',
      width: 130,
      min: 72,
      max: 400,
      onPreview: vi.fn((px: number) => Math.min(400, Math.max(72, px))),
      onCommit: vi.fn(),
      onReset: vi.fn(),
    };
    render(<ColumnResizeHandle {...props} />);
    return { props, handle: screen.getByRole('separator', { name: 'Resize Host column' }) };
  };

  it('is a focusable vertical separator whose value is the width', () => {
    const { handle } = setup();
    expect(handle).toHaveAttribute('aria-orientation', 'vertical');
    expect(handle).toHaveAttribute('aria-valuenow', '130');
    expect(handle).toHaveAttribute('aria-valuemin', '72');
    expect(handle).toHaveAttribute('aria-valuemax', '400');
    expect(handle).toHaveAttribute('tabindex', '0');
  });

  it('Left and Right move 8 px, Home and End go to the limits, Enter and a double-click reset', () => {
    const { props, handle } = setup();
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    fireEvent.keyDown(handle, { key: 'ArrowLeft' });
    fireEvent.keyDown(handle, { key: 'Home' });
    fireEvent.keyDown(handle, { key: 'End' });
    expect(props.onCommit.mock.calls.map((c) => c[0])).toEqual([138, 122, 72, 400]);
    fireEvent.keyDown(handle, { key: 'Enter' });
    fireEvent.doubleClick(handle);
    expect(props.onReset).toHaveBeenCalledTimes(2);
  });

  it('keeps the arrow keys from reaching a key handler around it', () => {
    const outer = vi.fn();
    render(
      <div onKeyDown={outer}>
        <ColumnResizeHandle label="App" width={130} min={112} max={400} onPreview={(px) => px} onCommit={vi.fn()} onReset={vi.fn()} />
      </div>
    );
    fireEvent.keyDown(screen.getByRole('separator', { name: 'Resize App column' }), { key: 'ArrowRight' });
    expect(outer).not.toHaveBeenCalled();
  });

  it('a drag previews each move and keeps the last width once, on release', () => {
    const { props, handle } = setup();
    handle.setPointerCapture = vi.fn();
    handle.hasPointerCapture = vi.fn(() => true);
    handle.releasePointerCapture = vi.fn();
    fireEvent.pointerDown(handle, { button: 0, buttons: 1, clientX: 500, pointerId: 1 });
    fireEvent.pointerMove(handle, { buttons: 1, clientX: 530, pointerId: 1 });
    fireEvent.pointerMove(handle, { buttons: 1, clientX: 560, pointerId: 1 });
    expect(props.onPreview.mock.calls.map((c) => c[0])).toEqual([160, 190]);
    expect(handle).toHaveAttribute('aria-valuenow', '190');
    expect(props.onCommit).not.toHaveBeenCalled();
    fireEvent.pointerUp(handle, { clientX: 560, pointerId: 1 });
    expect(props.onCommit).toHaveBeenCalledTimes(1);
    expect(props.onCommit).toHaveBeenCalledWith(190);
  });

  it('a move with no button held ends the drag where it was (a release lost outside the window)', () => {
    const { props, handle } = setup();
    handle.setPointerCapture = vi.fn();
    handle.hasPointerCapture = vi.fn(() => false);
    fireEvent.pointerDown(handle, { button: 0, buttons: 1, clientX: 500, pointerId: 1 });
    fireEvent.pointerMove(handle, { buttons: 1, clientX: 540, pointerId: 1 });
    fireEvent.pointerMove(handle, { buttons: 0, clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(handle, { buttons: 0, clientX: 0, pointerId: 1 });
    expect(props.onPreview).toHaveBeenCalledTimes(1);
    expect(props.onCommit).toHaveBeenCalledTimes(1);
    expect(props.onCommit).toHaveBeenCalledWith(170);
  });

  it('a click without movement keeps nothing', () => {
    const { props, handle } = setup();
    handle.setPointerCapture = vi.fn();
    handle.hasPointerCapture = vi.fn(() => false);
    fireEvent.pointerDown(handle, { button: 0, buttons: 1, clientX: 500, pointerId: 1 });
    fireEvent.pointerUp(handle, { clientX: 500, pointerId: 1 });
    expect(props.onCommit).not.toHaveBeenCalled();
  });
});

describe('Console column widths', () => {
  setupConsoleMocks();
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  const tableBox = () => screen.getByTestId('log-table-header').parentElement as HTMLElement;

  it('starts at the default widths, with a handle on Timestamp, Host and App only', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
    await screen.findByText('Nginx upstream connection timeout');
    const names = screen.getAllByRole('separator').map((s) => s.getAttribute('aria-label'));
    expect(names).toEqual(['Resize Timestamp column', 'Resize Host column', 'Resize App column']);
    expect(tableBox().style.getPropertyValue('--col-time')).toBe('120px');
    expect(tableBox().style.getPropertyValue('--col-host')).toBe('130px');
    expect(tableBox().style.getPropertyValue('--col-app')).toBe('130px');
    expect(screen.queryByRole('button', { name: 'Reset widths' })).toBeNull();
  });

  it('applies stored widths, ignoring ones out of range', async () => {
    localStorage.setItem(KEY, JSON.stringify({ time: 180, host: 5, app: 220 }));
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
    await screen.findByText('Nginx upstream connection timeout');
    expect(tableBox().style.getPropertyValue('--col-time')).toBe('180px');
    expect(tableBox().style.getPropertyValue('--col-host')).toBe('130px');
    expect(tableBox().style.getPropertyValue('--col-app')).toBe('220px');
    expect(screen.getByRole('button', { name: 'Reset widths' })).toBeInTheDocument();
  });

  it('a key press resizes the column for every row and remembers it; Reset widths forgets it', async () => {
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
    await screen.findByText('Nginx upstream connection timeout');
    const app = screen.getByRole('separator', { name: 'Resize App column' });
    fireEvent.keyDown(app, { key: 'ArrowRight' });
    await waitFor(() => expect(tableBox().style.getPropertyValue('--col-app')).toBe('138px'));
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}')).toEqual({ time: 120, host: 130, app: 138 });
    // Header and rows read the same variables
    const row = document.querySelector('.log-row') as HTMLElement;
    expect(row.className).toContain('var(--col-app,130px)');
    expect(screen.getByTestId('log-table-header').className).toContain('var(--col-app,130px)');

    fireEvent.click(within(screen.getByTestId('log-table-header')).getByRole('button', { name: 'Reset widths' }));
    await waitFor(() => expect(tableBox().style.getPropertyValue('--col-app')).toBe('130px'));
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reset widths' })).toBeNull();
  });

  it('works with the default widths when storage is blocked', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
    await screen.findByText('Nginx upstream connection timeout');
    expect(tableBox().style.getPropertyValue('--col-time')).toBe('120px');
    fireEvent.keyDown(screen.getByRole('separator', { name: 'Resize Timestamp column' }), { key: 'ArrowRight' });
    await waitFor(() => expect(tableBox().style.getPropertyValue('--col-time')).toBe('128px'));
  });
});
