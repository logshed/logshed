import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { CopyButton } from '../common/CopyButton.tsx';

export interface ConfigSnippetProps {
  title: string;
  subtitle: string;
  /** Where the snippet goes, shown above it. */
  instruction: React.ReactNode;
  snippet: string;
  /** Shown below the snippet. */
  note?: React.ReactNode;
}

/** A client configuration example: a collapsed header that opens to the snippet and a copy button. */
export const ConfigSnippet: React.FC<ConfigSnippetProps> = ({ title, subtitle, instruction, snippet, note }) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  return (
    <div className="bg-dark-950 border border-dark-800 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="w-full flex items-center justify-between p-3 bg-dark-950 hover:bg-dark-900/60 transition text-left cursor-pointer"
        aria-expanded={isExpanded}
      >
        <div className="flex items-center gap-2.5">
          {isExpanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          )}
          <div className="flex flex-col">
            <span className="text-xs font-semibold text-slate-200">
              {title}
            </span>
            <span className="text-[11px] text-slate-400 font-normal">
              {subtitle}
            </span>
          </div>
        </div>
        <span className="text-[11px] text-slate-400 font-mono">
          {isExpanded ? 'Collapse' : 'Expand'}
        </span>
      </button>

      {isExpanded && (
        <div className="p-3.5 border-t border-dark-800 bg-dark-900/30 space-y-2.5">
          <div className="flex items-center justify-between">
            <p className="text-[11px] text-slate-400">
              {instruction}
            </p>
            <CopyButton
              text={snippet}
              title="Copy JSON configuration"
              className="inline-flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 cursor-pointer"
              iconClassName="w-3 h-3"
              copiedLabelClassName="text-emerald-400"
            />
          </div>
          <pre className="p-2.5 bg-dark-900 border border-dark-800 rounded text-[11px] font-mono text-slate-300 overflow-x-auto leading-relaxed">
            {snippet}
          </pre>
          {note && (
            <p className="text-[11px] text-slate-400">
              {note}
            </p>
          )}
        </div>
      )}
    </div>
  );
};
