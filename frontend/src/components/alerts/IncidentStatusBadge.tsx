import React from 'react';
import { Sparkles, AlertTriangle } from 'lucide-react';

export interface IncidentStatusBadgeProps {
  aiEnrichment?: boolean;
  incidentSummary?: string | null;
}

export const IncidentStatusBadge: React.FC<IncidentStatusBadgeProps> = ({
  aiEnrichment,
  incidentSummary,
}) => {
  if (!aiEnrichment) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider bg-dark-900 border border-dark-700 text-slate-400">
        Triggered
      </span>
    );
  }

  const isFailed = Boolean(
    !incidentSummary ||
      incidentSummary.startsWith('AI analysis failed:') ||
      incidentSummary.startsWith('AI enrichment failed:')
  );

  if (isFailed) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider bg-red-950 text-red-400 border border-red-800">
        <AlertTriangle className="w-2.5 h-2.5" />
        <span>AI Failed</span>
        <span className="sr-only">Failed</span>
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider bg-purple-950 text-purple-300 border border-purple-800">
      <Sparkles className="w-2.5 h-2.5" />
      <span>AI Enriched</span>
      <span className="sr-only">Complete</span>
    </span>
  );
};
