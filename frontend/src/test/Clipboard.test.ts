import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { copyToClipboard } from '../utils/clipboard.ts';

describe('copyToClipboard', () => {
  const originalClipboard = navigator.clipboard;
  const originalIsSecureContext = window.isSecureContext;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    Object.defineProperty(window, 'isSecureContext', {
      value: originalIsSecureContext,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(navigator, 'clipboard', {
      value: originalClipboard,
      configurable: true,
      writable: true,
    });
  });

  it('returns false immediately for empty or null-like string', async () => {
    expect(await copyToClipboard('')).toBe(false);
  });

  it('uses navigator.clipboard.writeText when in secure context', async () => {
    Object.defineProperty(window, 'isSecureContext', {
      value: true,
      configurable: true,
    });

    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: writeTextMock },
      configurable: true,
    });

    const result = await copyToClipboard('test clipboard text');
    expect(result).toBe(true);
    expect(writeTextMock).toHaveBeenCalledWith('test clipboard text');
  });

  it('falls back to textarea execCommand when writeText fails', async () => {
    Object.defineProperty(window, 'isSecureContext', {
      value: true,
      configurable: true,
    });

    const writeTextMock = vi.fn().mockRejectedValue(new Error('Permission denied'));
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: writeTextMock },
      configurable: true,
    });

    const execCommandMock = vi.fn().mockReturnValue(true);
    document.execCommand = execCommandMock;

    const result = await copyToClipboard('fallback text');
    expect(result).toBe(true);
    expect(execCommandMock).toHaveBeenCalledWith('copy');
  });

  it('falls back to DOM range selection on span if textarea execCommand fails', async () => {
    Object.defineProperty(window, 'isSecureContext', {
      value: false,
      configurable: true,
    });

    let callCount = 0;
    const execCommandMock = vi.fn().mockImplementation(() => {
      callCount += 1;
      // First attempt (textarea) fails, second attempt (span) succeeds
      return callCount > 1;
    });
    document.execCommand = execCommandMock;

    const result = await copyToClipboard('span fallback text');
    expect(result).toBe(true);
    expect(execCommandMock).toHaveBeenCalledTimes(2);
  });

  it('returns false if both modern API and all fallbacks fail', async () => {
    Object.defineProperty(window, 'isSecureContext', {
      value: false,
      configurable: true,
    });

    document.execCommand = vi.fn().mockReturnValue(false);

    const result = await copyToClipboard('failing text');
    expect(result).toBe(false);
  });
});
