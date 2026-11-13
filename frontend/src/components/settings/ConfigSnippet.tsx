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
    <div className="bg-canvas border border-line-subtle rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="w-full flex items-center justify-between p-3 bg-canvas hover:bg-surface/60 transition text-left cursor-pointer"
        aria-expanded={isExpanded}
      >
        <div className="flex items-center gap-2.5">
          {isExpanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-fg-muted shrink-0" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-fg-muted shrink-0" />
          )}
          <div className="flex flex-col">
            <span className="text-xs font-semibold text-fg">
              {title}
            </span>
            <span className="text-[11px] text-fg-muted font-normal">
              {subtitle}
            </span>
          </div>
        </div>
        <span className="text-[11px] text-fg-muted font-mono">
          {isExpanded ? 'Collapse' : 'Expand'}
        </span>
      </button>

      {isExpanded && (
        <div className="p-3.5 border-t border-line-subtle bg-surface/30 space-y-2.5">
          <div className="flex items-center justify-between">
            <p className="text-[11px] text-fg-muted">
              {instruction}
            </p>
            <CopyButton
              text={snippet}
              title="Copy JSON configuration"
                            iconClassName="w-3 h-3"
              copiedLabelClassName="text-success-400"
            />
          </div>
          <pre className="p-2.5 bg-surface border border-line-subtle rounded-sm text-[11px] font-mono text-fg-soft overflow-x-auto leading-relaxed">
            {snippet}
          </pre>
          {note && (
            <p className="text-[11px] text-fg-muted">
              {note}
            </p>
          )}
        </div>
      )}
    </div>
  );
};
