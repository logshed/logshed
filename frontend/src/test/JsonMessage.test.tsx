import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { LogDetailModal } from '../components/logs/LogDetailModal.tsx';
import { JsonTree, JSON_TREE_MAX_ENTRIES } from '../components/logs/JsonTree.tsx';
import { JSON_MESSAGE_MAX_LENGTH, JSON_PREFIX_MAX_LENGTH, JsonValue, jsonValueToCopy, parseJsonMessage } from '../utils/jsonMessage.ts';
import { LogEntry } from '../types.ts';
import * as clipboardModule from '../utils/clipboard.ts';

describe('parseJsonMessage', () => {
  it('reads a message that is one JSON object', () => {
    expect(parseJsonMessage('{"level":"info","msg":"started","port":8080}')).toEqual({
      prefix: '',
      value: { level: 'info', msg: 'started', port: 8080 },
    });
  });

  it('reads nested objects and arrays', () => {
    const parsed = parseJsonMessage('{"request":{"method":"GET","headers":{"Host":["a.lan"]}},"status":200}');
    expect(parsed?.value).toEqual({ request: { method: 'GET', headers: { Host: ['a.lan'] } }, status: 200 });
  });

  it('keeps a short prefix apart from the object', () => {
    const parsed = parseJsonMessage('2026-11-15T10:00:00Z INFO {"msg":"ok"}');
    expect(parsed).toEqual({ prefix: '2026-11-15T10:00:00Z INFO', value: { msg: 'ok' } });
  });

  it('ignores colour codes and surrounding spaces', () => {
    expect(parseJsonMessage('  \u001b[32m{"msg":"green"}\u001b[0m  ')?.value).toEqual({ msg: 'green' });
  });

  it('leaves text, arrays, bare values and broken JSON as text', () => {
    for (const message of ['plain text', '[1,2,3]', '"a string"', '42', 'null', '{"msg": "cut off', '{"a":1} trailing', '{}x', '', null, undefined]) {
      expect(parseJsonMessage(message)).toBeNull();
    }
  });

  it('leaves an object after a long prefix as text', () => {
    const prefix = 'x'.repeat(JSON_PREFIX_MAX_LENGTH + 1);
    expect(parseJsonMessage(`${prefix}{"a":1}`)).toBeNull();
    expect(parseJsonMessage(`${'x'.repeat(JSON_PREFIX_MAX_LENGTH)}{"a":1}`)?.value).toEqual({ a: 1 });
  });

  it('leaves a very large message as text', () => {
    const big = `{"data":"${'a'.repeat(JSON_MESSAGE_MAX_LENGTH)}"}`;
    expect(parseJsonMessage(big)).toBeNull();
  });

  it('reads a deeply nested object, or gives up quietly when the engine cannot', () => {
    const depth = 500;
    const deep = '{"a":'.repeat(depth) + '1' + '}'.repeat(depth);
    const parsed = parseJsonMessage(deep);
    expect(parsed?.value).toHaveProperty('a');
    const tooDeep = '{"a":'.repeat(200_000) + '1' + '}'.repeat(200_000);
    expect(() => parseJsonMessage(tooDeep)).not.toThrow();
  });

  it('copies strings as they are and other values as indented JSON', () => {
    expect(jsonValueToCopy('GET /api')).toBe('GET /api');
    expect(jsonValueToCopy(200)).toBe('200');
    expect(jsonValueToCopy(null)).toBe('null');
    expect(jsonValueToCopy({ a: [1] })).toBe('{\n  "a": [\n    1\n  ]\n}');
  });
});

describe('JsonTree', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows keys and values, with two levels open', () => {
    render(<JsonTree value={{ msg: 'hi', req: { headers: { Host: 'a.lan' } } }} label="Message as JSON" />);
    const tree = screen.getByRole('list', { name: 'Message as JSON' });
    expect(tree).toHaveTextContent('msg: "hi"');
    expect(screen.getByRole('button', { name: /^req/ })).toHaveAttribute('aria-expanded', 'true');
    // The third level starts closed, with a count in place of its contents
    const headers = screen.getByRole('button', { name: /^headers/ });
    expect(headers).toHaveAttribute('aria-expanded', 'false');
    expect(headers).toHaveTextContent('{ 1 key }');
    expect(screen.queryByText('"a.lan"')).toBeNull();
    fireEvent.click(headers);
    expect(headers).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('"a.lan"')).toBeInTheDocument();
  });

  it('closes an open object', () => {
    render(<JsonTree value={{ req: { method: 'GET' } }} label="Message as JSON" />);
    const req = screen.getByRole('button', { name: /^req/ });
    fireEvent.click(req);
    expect(req).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('"GET"')).toBeNull();
  });

  it('names each copy button by the value it copies, and copies it', async () => {
    const copy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
    render(<JsonTree value={{ req: { tags: ['a', 'b'] }, msg: 'say "hi"' }} label="Message as JSON" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy value of msg' }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith('say "hi"'));
    fireEvent.click(screen.getByRole('button', { name: /^tags/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Copy value of req.tags[1]' }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith('b'));
    fireEvent.click(screen.getByRole('button', { name: 'Copy value of req' }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith('{\n  "tags": [\n    "a",\n    "b"\n  ]\n}'));
  });

  it('lists at most a fixed number of entries per object', () => {
    const value = Object.fromEntries(Array.from({ length: JSON_TREE_MAX_ENTRIES + 5 }, (_, i) => [`k${i}`, i]));
    render(<JsonTree value={value} label="Message as JSON" />);
    expect(screen.getAllByRole('button', { name: /^Copy value of k/ })).toHaveLength(JSON_TREE_MAX_ENTRIES);
    expect(screen.getByText('5 more not shown (see the text view)')).toBeInTheDocument();
  });

  it('shows numbers, booleans and null as written', () => {
    render(<JsonTree value={{ n: 1.5, t: true, z: null }} label="Message as JSON" />);
    const tree = screen.getByRole('list', { name: 'Message as JSON' });
    expect(tree).toHaveTextContent('n: 1.5');
    expect(tree).toHaveTextContent('t: true');
    expect(tree).toHaveTextContent('z: null');
  });
});

describe('JSON values as written', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows and copies a large integer and a written decimal as they are in the message', async () => {
    const parsed = parseJsonMessage('{"trace":12345678901234567890,"ratio":1.50,"n":7}');
    const copy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
    render(<JsonTree value={parsed!.value} label="Message as JSON" />);
    const tree = screen.getByRole('list', { name: 'Message as JSON' });
    expect(tree).toHaveTextContent('trace: 12345678901234567890');
    expect(tree).toHaveTextContent('ratio: 1.50');
    expect(tree).toHaveTextContent('n: 7');
    fireEvent.click(screen.getByRole('button', { name: 'Copy value of trace' }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith('12345678901234567890'));
    expect(jsonValueToCopy(parsed!.value)).toContain('"trace": 12345678901234567890');
  });

  it('a message nested past what the engine can read stays text, and never stops the page', () => {
    const depth = 20000;
    expect(parseJsonMessage('{"a":' + '['.repeat(depth) + '1' + ']'.repeat(depth) + '}')).toBeNull();
  });

  it('a value nested too deep to write copies nothing, and only when its button is pressed', async () => {
    let deep: JsonValue = 1;
    for (let i = 0; i < 20000; i++) deep = [deep];
    expect(jsonValueToCopy(deep)).toBe('');
    const copy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
    render(<JsonTree value={{ a: deep }} label="Message as JSON" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy value of a' }));
    await new Promise((r) => setTimeout(r, 0));
    expect(copy).not.toHaveBeenCalled();
  });
});

describe('Log drawer with a JSON message', () => {
  const jsonLog: LogEntry = {
    id: 7,
    timestamp: '2026-11-15T10:00:00.000Z',
    received_at: '2026-11-15T10:00:00.100Z',
    source_ip: '192.168.1.60',
    source_alias: 'docker-vm',
    app_name: 'caddy',
    facility: 1,
    severity: 6,
    message: 'INFO {"msg":"handled request","status":502,"request":{"uri":"/api"}}',
    raw: 'INFO {"msg":"handled request","status":502,"request":{"uri":"/api"}}',
  };
  const textLog: LogEntry = { ...jsonLog, id: 8, message: 'plain line', raw: 'plain line' };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the tree with its prefix, and the text view on request', () => {
    render(<LogDetailModal log={jsonLog} isOpen onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    const tree = screen.getByRole('list', { name: 'Message as JSON' });
    expect(within(tree).getByText('"handled request"')).toBeInTheDocument();
    expect(tree.parentElement?.firstElementChild).toHaveTextContent(/^INFO$/);
    const treeButton = screen.getByRole('button', { name: 'Tree' });
    const textButton = screen.getByRole('button', { name: 'Text' });
    expect(treeButton).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(textButton);
    expect(textButton).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('list', { name: 'Message as JSON' })).toBeNull();
    expect(screen.getAllByText(jsonLog.message).length).toBeGreaterThan(0);
  });

  it('keeps the chosen view when the drawer moves to another JSON log', () => {
    const { rerender } = render(<LogDetailModal log={jsonLog} isOpen onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Text' }));
    rerender(<LogDetailModal log={{ ...jsonLog, id: 9 }} isOpen onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Text' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('list', { name: 'Message as JSON' })).toBeNull();
    // Closing the drawer goes back to the tree for the next log opened
    rerender(<LogDetailModal log={{ ...jsonLog, id: 9 }} isOpen={false} onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    rerender(<LogDetailModal log={jsonLog} isOpen onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Tree' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('offers no tree for a plain message', () => {
    render(<LogDetailModal log={textLog} isOpen onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Tree' })).toBeNull();
    expect(screen.queryByRole('list', { name: 'Message as JSON' })).toBeNull();
    expect(screen.getAllByText('plain line').length).toBeGreaterThan(0);
  });

  it('Up and Down in the message box scroll it instead of stepping to another log', () => {
    const onNavigateNewer = vi.fn();
    const onNavigateOlder = vi.fn();
    render(
      <LogDetailModal log={textLog} isOpen onClose={vi.fn()} onExplainWithAi={vi.fn()} onNavigateNewer={onNavigateNewer} onNavigateOlder={onNavigateOlder} hasNewerLog hasOlderLog />
    );
    const box = screen.getByRole('region', { name: 'Message text' });
    box.focus();
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    fireEvent.keyDown(box, { key: 'ArrowUp' });
    expect(onNavigateOlder).not.toHaveBeenCalled();
    expect(onNavigateNewer).not.toHaveBeenCalled();
    // Elsewhere in the drawer the keys still step
    const heading = screen.getByRole('button', { name: /Older/ });
    heading.focus();
    fireEvent.keyDown(heading, { key: 'ArrowDown' });
    expect(onNavigateOlder).toHaveBeenCalledTimes(1);
  });

  it('still copies the whole message', async () => {
    const copy = vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
    render(<LogDetailModal log={jsonLog} isOpen onClose={vi.fn()} onExplainWithAi={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /Copy Message/ }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith(jsonLog.message));
  });
});
