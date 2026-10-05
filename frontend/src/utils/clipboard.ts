/**
 * Cross-browser clipboard copy helper with fallback for non-secure HTTP / LAN origins.
 * (navigator.clipboard is restricted to HTTPS and localhost; document.execCommand fallback
 * ensures clipboard works when accessing over local IP http://192.168.x.x:8080).
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false;

  // 1. Try modern async Clipboard API if available and in secure context
  if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      console.warn('navigator.clipboard.writeText failed, attempting fallback copy:', err);
    }
  }

  // 2. Fallback for non-secure origins, embedded frames, or when writeText rejects
  try {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.top = '0';
    textArea.style.left = '-9999px';
    textArea.style.width = '2em';
    textArea.style.height = '2em';
    textArea.style.padding = '0';
    textArea.style.border = 'none';
    textArea.style.outline = 'none';
    textArea.style.boxShadow = 'none';
    textArea.style.background = 'transparent';
    textArea.style.opacity = '0';
    textArea.style.pointerEvents = 'none';
    textArea.setAttribute('aria-hidden', 'true');
    textArea.tabIndex = -1;

    document.body.appendChild(textArea);
    try {
      textArea.focus();
      textArea.select();
      textArea.setSelectionRange(0, text.length);
      if (document.execCommand('copy')) {
        return true;
      }
    } finally {
      if (textArea.parentNode) {
        document.body.removeChild(textArea);
      }
    }

    // Secondary fallback using DOM range selection on a pre-formatted element (mobile WebKit friendly)
    const span = document.createElement('span');
    span.textContent = text;
    span.style.whiteSpace = 'pre';
    span.style.position = 'fixed';
    span.style.top = '0';
    span.style.left = '-9999px';
    span.style.opacity = '0';
    span.style.pointerEvents = 'none';
    span.setAttribute('aria-hidden', 'true');

    document.body.appendChild(span);
    try {
      const selection = window.getSelection();
      if (selection) {
        const range = document.createRange();
        range.selectNode(span);
        selection.removeAllRanges();
        selection.addRange(range);
        const successful = document.execCommand('copy');
        selection.removeAllRanges();
        return successful;
      }
    } finally {
      if (span.parentNode) {
        document.body.removeChild(span);
      }
    }
    return false;
  } catch (err) {
    console.error('Fallback clipboard copy failed:', err);
    return false;
  }
}
