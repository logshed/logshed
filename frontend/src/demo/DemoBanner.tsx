import React, { useState } from 'react';

/** A small note over the app that says this is a demo with made-up data. It floats, so the app's layout is unchanged. */
export const DemoBanner: React.FC = () => {
  const [open, setOpen] = useState(true);
  if (!open) return null;
  return (
    <div role="note" className="fixed bottom-[4.5rem] sm:bottom-3 left-3 right-3 sm:right-auto sm:max-w-md z-[100] rounded-lg bg-sky-950/95 border border-sky-700 text-sky-100 text-xs sm:text-sm px-3 py-2 shadow-lg flex items-start gap-3">
      <p className="flex-1">
        <strong className="font-semibold">LogShed demo.</strong> The logs, hosts and AI answers are made up, nothing leaves your browser, and your changes reset when you reload.{' '}
        <a href="https://logshed.dev/#quickstart" className="underline hover:text-white">Install LogShed</a>
      </p>
      <button type="button" onClick={() => setOpen(false)} className="text-sky-300 hover:text-white px-1" aria-label="Hide the demo note">×</button>
    </div>
  );
};
