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
      <span className="bg-emerald-950/60 border border-emerald-800/80 text-emerald-300 px-1.5 py-0.5 rounded">
        ↓ {tokensIn.toLocaleString()} in
      </span>
    )}
    {tokensOut != null && (
      <span className="bg-sky-950/60 border border-sky-800/80 text-sky-300 px-1.5 py-0.5 rounded">
        ↑ {tokensOut.toLocaleString()} out
      </span>
    )}
    {Boolean(tokensThoughts) && (
      <span className="bg-purple-950/60 border border-purple-800/80 text-purple-300 px-1.5 py-0.5 rounded">
        ⚡ {tokensThoughts!.toLocaleString()} thinking
      </span>
    )}
  </>
);
