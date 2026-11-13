/**
 * iOS Safari zooms the page when a text field with text under 16 px gets focus. On iPhones and iPads
 * the viewport gets maximum-scale=1, which stops that zoom; iOS ignores maximum-scale for pinch zoom
 * (an accessibility rule since iOS 10), so pinch zoom still works there. Other browsers keep the
 * plain viewport, because Android honours maximum-scale and would lose pinch zoom.
 */

interface NavigatorLike {
  userAgent: string;
  maxTouchPoints: number;
}

/** An iPhone, iPod or iPad, including an iPad that reports itself as a Mac (a Mac has no touch screen). */
export function isIosDevice(nav: NavigatorLike): boolean {
  return /iPhone|iPad|iPod/.test(nav.userAgent) || (/Macintosh/.test(nav.userAgent) && nav.maxTouchPoints > 1);
}

/** Adds maximum-scale=1 to the viewport meta tag on iOS; leaves it alone everywhere else. */
export function preventIosFocusZoom(doc: Document = document, nav: NavigatorLike = navigator): void {
  if (!isIosDevice(nav)) return;
  const meta = doc.querySelector('meta[name="viewport"]');
  const content = meta?.getAttribute('content');
  if (!meta || content == null || /maximum-scale/.test(content)) return;
  meta.setAttribute('content', `${content}, maximum-scale=1`);
}
