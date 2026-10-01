import React from 'react';
import { RefreshCw } from 'lucide-react';

export interface LoadingSpinnerProps {
  message?: string;
  className?: string;
}

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
  message = 'Loading...',
  className = 'p-8 text-center text-slate-500 font-mono text-xs flex items-center justify-center gap-2',
}) => {
  return (
    <div className={className}>
      <RefreshCw className="w-4 h-4 animate-spin text-accent-500" />
      {message ? <span>{message}</span> : null}
    </div>
  );
};

export default LoadingSpinner;
