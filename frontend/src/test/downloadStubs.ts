import { vi } from 'vitest';

/**
 * jsdom has no object URLs, so downloadBlob throws there and an export test would only exercise the
 * error path. Stubs them (createObjectURL returns 'blob:export') and the anchor click for a test
 * that downloads a file. Call the returned function after the URL was revoked to restore them.
 */
export function stubDownloads(): () => void {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn(() => 'blob:export');
  URL.revokeObjectURL = vi.fn();
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  return () => {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    click.mockRestore();
  };
}
