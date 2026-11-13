import { createLucideIcon } from 'lucide-react';

/**
 * The key icon as lucide-react 0.x drew it: ring at the bottom left, teeth at the top right.
 * lucide-react 1.x mirrored its own Key across the diagonal; this local copy keeps the drawing
 * LogShed has always shown, whatever later lucide releases do.
 */
export const Key = createLucideIcon('key', [
  ['path', { d: 'm15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4', key: 'shaft-teeth' }],
  ['path', { d: 'm21 2-9.6 9.6', key: 'shaft' }],
  ['circle', { cx: '7.5', cy: '15.5', r: '5.5', key: 'ring' }],
]);
