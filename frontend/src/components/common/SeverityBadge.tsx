import React from 'react';

interface SeverityBadgeProps {
  severity: number;
  className?: string;
}

export interface SeverityInfo {
  label: string;
  shortLabel: string;
  bgClass: string;
  textClass: string;
  borderClass: string;
}

export const SEVERITIES: { value: number; name: string }[] = [
  { value: 0, name: 'Emergency' },
  { value: 1, name: 'Alert' },
  { value: 2, name: 'Critical' },
  { value: 3, name: 'Error' },
  { value: 4, name: 'Warning' },
  { value: 5, name: 'Notice' },
  { value: 6, name: 'Info' },
  { value: 7, name: 'Debug' },
];

export function getSeverityInfo(severity: number): SeverityInfo {
  switch (severity) {
    case 0:
      return {
        label: 'Emergency',
        shortLabel: 'EMERG',
        bgClass: 'bg-danger-950/80',
        textClass: 'text-danger-400',
        borderClass: 'border-danger-800',
      };
    case 1:
      return {
        label: 'Alert',
        shortLabel: 'ALERT',
        bgClass: 'bg-danger-950/80',
        textClass: 'text-danger-400',
        borderClass: 'border-danger-800',
      };
    case 2:
      return {
        label: 'Critical',
        shortLabel: 'CRIT',
        bgClass: 'bg-danger-950/80',
        textClass: 'text-danger-400',
        borderClass: 'border-danger-800',
      };
    case 3:
      return {
        label: 'Error',
        shortLabel: 'ERROR',
        bgClass: 'bg-danger-900/60',
        textClass: 'text-danger-300',
        borderClass: 'border-danger-700',
      };
    case 4:
      return {
        label: 'Warning',
        shortLabel: 'WARN',
        bgClass: 'bg-warning-950/80',
        textClass: 'text-warning-300',
        borderClass: 'border-warning-800',
      };
    case 5:
      return {
        label: 'Notice',
        shortLabel: 'NOTICE',
        bgClass: 'bg-info-950/80',
        textClass: 'text-info-300',
        borderClass: 'border-info-800',
      };
    case 6:
      return {
        label: 'Info',
        shortLabel: 'INFO',
        bgClass: 'bg-neutral-800/80',
        textClass: 'text-fg-soft',
        borderClass: 'border-line-strong',
      };
    case 7:
    default:
      return {
        label: 'Debug',
        shortLabel: 'DEBUG',
        bgClass: 'bg-neutral-900/80',
        textClass: 'text-fg-muted',
        borderClass: 'border-line',
      };
  }
}

export const SeverityBadge: React.FC<SeverityBadgeProps> = ({ severity, className = '' }) => {
  const info = getSeverityInfo(severity);

  return (
    <span
      data-testid="severity-badge"
      data-severity={severity}
      title={`${info.label} (Level ${severity})`}
      className={`inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-mono font-medium rounded-sm border uppercase tracking-wider ${info.bgClass} ${info.textClass} ${info.borderClass} ${className}`}
    >
      {info.shortLabel}
    </span>
  );
};
