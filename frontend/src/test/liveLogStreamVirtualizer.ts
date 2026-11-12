// The virtualizer stand-in for the LiveLogStream tests. It imports nothing from the app, so the
// vi.mock factory that loads it cannot run into an import cycle. Each test file mocks with
// vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock).
import { vi, type Mock } from 'vitest';

// Test control for the virtualizer mock: when edgeRows is set, only the first and last
// edgeRows rows are "visible" (like a real viewport), so large buffers stay cheap to render.
// headOnly hides the tail too, so older pages load only through scroll events.
export const virtualizerControl: {
  edgeRows: number | null;
  headOnly: boolean;
  lastOptions: any;
  scrollToIndex: Mock<(...args: unknown[]) => void>;
} = {
  edgeRows: null,
  headOnly: false,
  lastOptions: null,
  scrollToIndex: vi.fn(),
};

export const virtualizerMock = {
  useVirtualizer: (options: { count: number; estimateSize?: () => number; getItemKey?: (i: number) => unknown }) => {
    const { count, estimateSize, getItemKey } = options;
    virtualizerControl.lastOptions = options;
    const size = estimateSize ? estimateSize() : 28;
    const edge = virtualizerControl.edgeRows;
    const indexes =
      edge === null || count <= edge * 2
        ? Array.from({ length: count }, (_, i) => i)
        : [
            ...Array.from({ length: edge }, (_, i) => i),
            ...(virtualizerControl.headOnly ? [] : Array.from({ length: edge }, (_, i) => count - edge + i)),
          ];
    return {
      measure: () => {},
      getTotalSize: () => count * size,
      getVirtualItems: () =>
        indexes.map((index) => ({
          key: getItemKey ? getItemKey(index) : index,
          index,
          start: index * size,
          size,
        })),
      scrollToIndex: (...args: unknown[]) => virtualizerControl.scrollToIndex(...args),
    };
  },
};
