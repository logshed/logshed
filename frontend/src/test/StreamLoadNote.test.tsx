import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { StreamLoadNote, getStreamLoadText } from '../components/logs/StreamLoadNote.tsx';

describe('StreamLoadNote', () => {
  it('says the count may include logs the search hides while a search is set', () => {
    expect(getStreamLoadText(5, null, true)).toBe(
      'High load: 5 live updates skipped (not all of them match your search). Every log is still stored; search again to see them.',
    );
    expect(getStreamLoadText(0, 'queue')).toBeNull();
  });

  it('announces a sentence without the changing count, and shows the count', () => {
    const { rerender } = render(<StreamLoadNote skipped={10} nearLimit={null} />);
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('High load: live updates are being skipped. Every log is still stored.');
    expect(status).not.toHaveTextContent('10');
    rerender(<StreamLoadNote skipped={20} nearLimit={null} />);
    expect(screen.getByRole('status')).toHaveTextContent('High load: live updates are being skipped. Every log is still stored.');
    expect(screen.getByTestId('stream-load-note')).toHaveTextContent('20 live updates skipped');
  });
});
