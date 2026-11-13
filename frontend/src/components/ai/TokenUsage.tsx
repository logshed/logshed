import React from 'react';

export interface TokenUsageProps {
  tokensIn?: number | null;
  tokensOut?: number | null;
  tokensThoughts?: number | null;
}

/** The tokens an AI call used, as pills: sent, received and (when any) spent thinking. */
export const TokenUsage: React.FC<TokenUsageProps> = ({ tokensIn, tokensOut, tokensThoughts }) => (
  <>
    {tokensIn != null && (
      <span className="bg-success-950/60 border border-success-800/80 text-success-300 px-1.5 py-0.5 rounded-sm">
        ↓ {tokensIn.toLocaleString()} in
      </span>
    )}
    {tokensOut != null && (
      <span className="bg-accent-950/60 border border-accent-800/80 text-accent-300 px-1.5 py-0.5 rounded-sm">
        ↑ {tokensOut.toLocaleString()} out
      </span>
    )}
    {Boolean(tokensThoughts) && (
      <span className="bg-highlight-950/60 border border-highlight-800/80 text-highlight-300 px-1.5 py-0.5 rounded-sm">
        ⚡ {tokensThoughts!.toLocaleString()} thinking
      </span>
    )}
  </>
);
