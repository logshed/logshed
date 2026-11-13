import React, { useState } from 'react';

/** A small note over the app that says this is a demo with made-up data. It floats, so the app's layout is unchanged. */
export const DemoBanner: React.FC = () => {
  const [open, setOpen] = useState(true);
  if (!open) return null;
  return (
    <div role="note" className="fixed bottom-18 sm:bottom-3 left-3 right-3 sm:right-auto sm:max-w-md z-100 rounded-lg bg-accent-950/95 border border-accent-700 text-accent-100 text-xs sm:text-sm px-3 py-2 shadow-lg flex items-start gap-3">
      <p className="flex-1">
        <strong className="font-semibold">LogShed demo.</strong> The logs, hosts and AI answers are made up, nothing leaves your browser, and your changes reset when you reload.{' '}
        <a href="https://logshed.dev/#quickstart" className="underline hover:text-fg-max">Install LogShed</a>
      </p>
      <button type="button" onClick={() => setOpen(false)} className="text-accent-300 hover:text-fg-max px-1" aria-label="Hide the demo note">×</button>
    </div>
  );
};
