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
        bgClass: 'bg-red-950/80',
        textClass: 'text-red-400',
        borderClass: 'border-red-800',
      };
    case 1:
      return {
        label: 'Alert',
        shortLabel: 'ALERT',
        bgClass: 'bg-red-950/80',
        textClass: 'text-red-400',
        borderClass: 'border-red-800',
      };
    case 2:
      return {
        label: 'Critical',
        shortLabel: 'CRIT',
        bgClass: 'bg-red-950/80',
        textClass: 'text-red-400',
        borderClass: 'border-red-800',
      };
    case 3:
      return {
        label: 'Error',
        shortLabel: 'ERROR',
        bgClass: 'bg-red-900/60',
        textClass: 'text-red-300',
        borderClass: 'border-red-700',
      };
    case 4:
      return {
        label: 'Warning',
        shortLabel: 'WARN',
        bgClass: 'bg-amber-950/80',
        textClass: 'text-amber-300',
        borderClass: 'border-amber-800',
      };
    case 5:
      return {
        label: 'Notice',
        shortLabel: 'NOTICE',
        bgClass: 'bg-blue-950/80',
        textClass: 'text-blue-300',
        borderClass: 'border-blue-800',
      };
    case 6:
      return {
        label: 'Info',
        shortLabel: 'INFO',
        bgClass: 'bg-slate-800/80',
        textClass: 'text-slate-300',
        borderClass: 'border-slate-700',
      };
    case 7:
    default:
      return {
        label: 'Debug',
        shortLabel: 'DEBUG',
        bgClass: 'bg-slate-900/80',
        textClass: 'text-slate-400',
        borderClass: 'border-slate-800',
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
      className={`inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-mono font-medium rounded border uppercase tracking-wider ${info.bgClass} ${info.textClass} ${info.borderClass} ${className}`}
    >
      {info.shortLabel}
    </span>
  );
};
