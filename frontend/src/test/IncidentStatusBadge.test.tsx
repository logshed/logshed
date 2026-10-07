import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { IncidentStatusBadge } from '../components/alerts/IncidentStatusBadge.tsx';

describe('IncidentStatusBadge Component', () => {
  it('renders Triggered badge when aiEnrichment is false or undefined', () => {
    const { rerender } = render(<IncidentStatusBadge aiEnrichment={false} />);
    expect(screen.getByText('Triggered')).toBeInTheDocument();

    rerender(<IncidentStatusBadge />);
    expect(screen.getByText('Triggered')).toBeInTheDocument();
  });

  it('renders AI Enriched badge when aiEnrichment is true and summary succeeded', () => {
    render(
      <IncidentStatusBadge
        aiEnrichment={true}
        incidentSummary="Root cause identified as memory pressure."
      />
    );
    expect(screen.getByText('AI Enriched')).toBeInTheDocument();
    expect(screen.getByText('Complete')).toBeInTheDocument();
  });

  it('renders AI Failed badge when incidentSummary is null or empty', () => {
    render(<IncidentStatusBadge aiEnrichment={true} incidentSummary={null} />);
    expect(screen.getByText('AI Failed')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('renders AI Failed badge when incidentSummary indicates AI failure', () => {
    const { rerender } = render(
      <IncidentStatusBadge
        aiEnrichment={true}
        incidentSummary="AI analysis failed: 504 Deadline Exceeded"
      />
    );
    expect(screen.getByText('AI Failed')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();

    rerender(
      <IncidentStatusBadge
        aiEnrichment={true}
        incidentSummary="AI enrichment failed: rate limit"
      />
    );
    expect(screen.getByText('AI Failed')).toBeInTheDocument();
  });
});
