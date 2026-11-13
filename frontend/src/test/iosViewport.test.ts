import { afterEach, describe, expect, it } from 'vitest';
import { isIosDevice, preventIosFocusZoom } from '../utils/iosViewport.ts';

const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1';
const IPAD_AS_MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15';
const ANDROID = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36';
const PLAIN = 'width=device-width, initial-scale=1.0';

function viewport(content = PLAIN): HTMLMetaElement {
  const meta = document.createElement('meta');
  meta.name = 'viewport';
  meta.content = content;
  document.head.appendChild(meta);
  return meta;
}

describe('iOS focus zoom', () => {
  afterEach(() => {
    document.head.querySelectorAll('meta[name="viewport"]').forEach((m) => m.remove());
  });

  it('knows iPhones and iPads, including an iPad that reports itself as a Mac', () => {
    expect(isIosDevice({ userAgent: IPHONE, maxTouchPoints: 5 })).toBe(true);
    expect(isIosDevice({ userAgent: IPAD_AS_MAC, maxTouchPoints: 5 })).toBe(true);
    expect(isIosDevice({ userAgent: IPAD_AS_MAC, maxTouchPoints: 0 })).toBe(false);
    expect(isIosDevice({ userAgent: ANDROID, maxTouchPoints: 5 })).toBe(false);
  });

  it('stops the zoom on focus on iOS, where pinch zoom still works', () => {
    const meta = viewport();
    preventIosFocusZoom(document, { userAgent: IPHONE, maxTouchPoints: 5 });
    expect(meta.content).toBe(`${PLAIN}, maximum-scale=1`);
    preventIosFocusZoom(document, { userAgent: IPHONE, maxTouchPoints: 5 });
    expect(meta.content).toBe(`${PLAIN}, maximum-scale=1`);
  });

  it('leaves the viewport alone elsewhere, because Android would block pinch zoom', () => {
    const meta = viewport();
    preventIosFocusZoom(document, { userAgent: ANDROID, maxTouchPoints: 5 });
    expect(meta.content).toBe(PLAIN);
  });
});
