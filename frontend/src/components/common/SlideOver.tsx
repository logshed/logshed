import React from 'react';
import { X } from 'lucide-react';
import { useEscapeKey } from '../../utils/hooks.ts';

interface SlideOverProps {
  isOpen: boolean;
  onClose: () => void;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  headerActions?: React.ReactNode;
  children: React.ReactNode;
  width?: string;
}

export const SlideOver: React.FC<SlideOverProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  headerActions,
  children,
  width = 'max-w-3xl',
}) => {
  useEscapeKey(isOpen, onClose);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop overlay */}
      <div
        data-testid="slideover-backdrop"
        className="fixed inset-0 bg-black/60 backdrop-blur-xs transition-opacity cursor-pointer"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="fixed inset-y-0 right-0 flex max-w-full pl-0 sm:pl-10 pointer-events-none">
          <div
            className={`pointer-events-auto w-screen ${width} bg-dark-900 border-l border-dark-700 shadow-2xl flex flex-col overscroll-contain animate-in slide-in-from-right duration-200 overflow-x-hidden`}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-dark-700 bg-dark-950 shrink-0 gap-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-slate-200 tracking-wide truncate">{title}</div>
                {subtitle && (
                  <div className="mt-0.5">{subtitle}</div>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {headerActions}
                <button
                  onClick={onClose}
                  className="text-slate-400 hover:text-slate-200 hover:bg-dark-800 p-1.5 rounded transition shrink-0 min-w-[36px] min-h-[36px] flex items-center justify-center cursor-pointer"
                  aria-label="Close panel"
                >
                  <X className="w-5 h-5 sm:w-4 sm:h-4" />
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="p-4 overflow-y-auto flex-1 overscroll-contain overflow-x-hidden">{children}</div>
          </div>
        </div>
      </div>
    </div>
  );
};
