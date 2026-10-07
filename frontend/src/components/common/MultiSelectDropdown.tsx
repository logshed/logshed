import React, { useState, useRef, useEffect, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, Check, X, Search, Plus } from 'lucide-react';

export interface MultiSelectDropdownProps {
  label: string;
  options: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
  placeholder?: string;
  icon?: React.ReactNode;
  allowCustomInput?: boolean;
  variant?: 'filter' | 'form';
}

export const MultiSelectDropdown: React.FC<MultiSelectDropdownProps> = ({
  label,
  options,
  selected,
  onChange,
  placeholder = 'All',
  icon,
  allowCustomInput = true,
  variant = 'filter',
}) => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [searchTerm, setSearchTerm] = useState<string>('');
  const [coords, setCoords] = useState<{ top: number; left: number; width: number } | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const isForm = variant === 'form';

  const updatePosition = () => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    let top = rect.bottom + 4;
    let left = rect.left;
    const width = isForm ? Math.max(rect.width, 240) : 256;
    const height = popoverRef.current ? popoverRef.current.offsetHeight : 300;

    if (typeof window !== 'undefined') {
      if (left + width > window.innerWidth - 12) {
        left = Math.max(12, window.innerWidth - width - 12);
      }
      if (top + height > window.innerHeight && rect.top > height) {
        top = Math.max(8, rect.top - height - 4);
      }
    }

    setCoords({ top, left, width });
  };

  const handleToggleOpen = () => {
    if (!isOpen) {
      updatePosition();
      setIsOpen(true);
    } else {
      setIsOpen(false);
    }
  };

  // Sync position on open
  useLayoutEffect(() => {
    if (isOpen) {
      updatePosition();
    }
  }, [isOpen]);

  // Update position on window scroll or resize
  useEffect(() => {
    if (!isOpen) return;

    let rafId: number | null = null;
    const handleScrollOrResize = () => {
      if (rafId !== null) return;
      rafId = window.requestAnimationFrame(() => {
        rafId = null;
        updatePosition();
      });
    };

    window.addEventListener('resize', handleScrollOrResize, { passive: true });
    window.addEventListener('scroll', handleScrollOrResize, { passive: true, capture: true });
    return () => {
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId);
      }
      window.removeEventListener('resize', handleScrollOrResize);
      window.removeEventListener('scroll', handleScrollOrResize, true);
    };
  }, [isOpen]);

  // Close on click outside (inspects both trigger container and portaled popover)
  useEffect(() => {
    if (!isOpen) return;
    const handleOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        containerRef.current &&
        !containerRef.current.contains(target) &&
        popoverRef.current &&
        !popoverRef.current.contains(target)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleOutsideClick);
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
    };
  }, [isOpen]);

  // Focus search input when opened
  useEffect(() => {
    if (isOpen) {
      const timer = setTimeout(() => {
        searchInputRef.current?.focus();
      }, 0);
      return () => clearTimeout(timer);
    } else {
      setSearchTerm('');
    }
  }, [isOpen]);

  // Handle escape key (capture so it closes dropdown without closing parent modal)
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setIsOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [isOpen]);

  const toggleOption = (option: string) => {
    if (selected.includes(option)) {
      onChange(selected.filter((item) => item !== option));
    } else {
      onChange([...selected, option]);
    }
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange([]);
  };

  const handleSelectAll = () => {
    const filtered = options.filter((opt) =>
      opt.toLowerCase().includes(searchTerm.toLowerCase())
    );
    const combined = Array.from(new Set([...selected, ...filtered]));
    onChange(combined);
  };

  const handleAddCustom = () => {
    const trimmed = searchTerm.trim();
    if (trimmed && !selected.includes(trimmed)) {
      onChange([...selected, trimmed]);
      setSearchTerm('');
    }
  };

  // Ensure any currently selected items are always included in the list so the user can see and toggle them
  const allOptions = React.useMemo(() => {
    const set = new Set(options);
    selected.forEach((s) => set.add(s));
    return Array.from(set);
  }, [options, selected]);

  // Filter options based on search term
  const filteredOptions = allOptions.filter((opt) =>
    opt.toLowerCase().includes(searchTerm.toLowerCase())
  );

  // Determine button summary text
  const getSummaryText = () => {
    if (selected.length === 0) return placeholder;
    if (selected.length === 1) return selected[0];
    return `${selected[0]} (+${selected.length - 1})`;
  };

  const hasSearchTerm = searchTerm.trim().length > 0;
  const isSearchTermNew =
    hasSearchTerm &&
    !options.some((opt) => opt.toLowerCase() === searchTerm.trim().toLowerCase()) &&
    !selected.some((opt) => opt.toLowerCase() === searchTerm.trim().toLowerCase());

  const renderDropdownMenu = () => (
    <>
      {/* Search Box */}
      <div className="px-2 pb-1.5 border-b border-dark-800">
        <div className="relative">
          <input
            ref={searchInputRef}
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && allowCustomInput && isSearchTermNew) {
                e.preventDefault();
                handleAddCustom();
              }
            }}
            placeholder={`Search or add ${label.toLowerCase()}...`}
            className="w-full bg-dark-950 border border-dark-700 rounded px-2 py-1 pl-6 text-[11px] text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
          />
          <Search className="w-3 h-3 text-slate-500 absolute left-2 top-2" />
          {searchTerm && (
            <button
              type="button"
              onClick={() => setSearchTerm('')}
              className="absolute right-1.5 top-1.5 text-slate-500 hover:text-slate-300"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>

      {/* Quick Header Actions */}
      <div className="flex items-center justify-between px-2.5 py-1 text-[10px] text-slate-400 border-b border-dark-800 bg-dark-950/50">
        <span>
          {selected.length} of {options.length} selected
        </span>
        <div className="flex items-center gap-2">
          {filteredOptions.length > 0 && (
            <button
              type="button"
              onClick={handleSelectAll}
              className="hover:text-accent-400 text-slate-400 transition"
            >
              Select All
            </button>
          )}
          {selected.length > 0 && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="hover:text-amber-400 text-slate-400 transition"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Scrollable Option Items */}
      <div className="max-h-60 overflow-y-auto py-1 font-mono text-xs">
        {/* Custom Input Option if typed value not in list */}
        {allowCustomInput && isSearchTermNew && (
          <div
            onClick={handleAddCustom}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                handleAddCustom();
              }
            }}
            role="button"
            tabIndex={0}
            className="flex items-center gap-2 px-2.5 py-1.5 cursor-pointer bg-dark-800/60 hover:bg-accent-950/60 text-accent-300 text-[11px] border-b border-dark-800 focus:outline-hidden focus:bg-accent-950/60"
          >
            <Plus className="w-3 h-3 text-accent-400 shrink-0" />
            <span className="truncate">Add "{searchTerm.trim()}"</span>
          </div>
        )}

        {filteredOptions.length === 0 && !isSearchTermNew ? (
          <div className="px-3 py-3 text-center text-slate-500 text-[11px]">
            {options.length === 0 ? `No ${label.toLowerCase()} discovered yet` : 'No matching items'}
          </div>
        ) : (
          filteredOptions.map((option) => {
            const isChecked = selected.includes(option);
            return (
              <div
                key={option}
                onClick={() => toggleOption(option)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggleOption(option);
                  }
                }}
                role="button"
                tabIndex={0}
                aria-pressed={isChecked}
                className={`flex items-center justify-between px-2.5 py-1 cursor-pointer select-none text-[11px] transition focus:outline-hidden focus:bg-dark-800 ${
                  isChecked
                    ? 'bg-accent-950/50 text-accent-200'
                    : 'text-slate-300 hover:bg-dark-800 hover:text-slate-100'
                }`}
              >
                <span className="truncate pr-2" title={option}>
                  {option}
                </span>
                <div
                  className={`w-3.5 h-3.5 rounded flex items-center justify-center border transition shrink-0 ${
                    isChecked
                      ? 'bg-accent-600 border-accent-500 text-white'
                      : 'border-dark-600 bg-dark-950'
                  }`}
                >
                  {isChecked && <Check className="w-2.5 h-2.5" />}
                </div>
              </div>
            );
          })
        )}
      </div>
    </>
  );

  return (
    <div
      className={`relative text-left ${isForm ? 'w-full block' : 'inline-block'}`}
      ref={containerRef}
    >
      {/* Dropdown Trigger Button */}
      <div
        onClick={handleToggleOpen}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleToggleOpen();
          }
        }}
        className={
          isForm
            ? `w-full h-[38px] flex items-center justify-between bg-dark-800 border rounded-lg px-3 text-xs cursor-pointer select-none transition focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20 ${
                isOpen
                  ? 'border-accent-500 ring-1 ring-accent-500/20'
                  : 'border-dark-700 hover:border-slate-600 text-slate-200'
              }`
            : `flex items-center gap-1.5 bg-dark-900 border rounded px-2.5 py-1 text-xs cursor-pointer select-none transition focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20 ${
                isOpen
                  ? 'border-accent-500 ring-1 ring-accent-500/20'
                  : selected.length > 0
                  ? 'border-accent-600/60 bg-dark-900/90 text-slate-100'
                  : 'border-dark-700 hover:border-slate-600 text-slate-300'
              }`
        }
        role="button"
        tabIndex={0}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        {isForm ? (
          <>
            <div className="flex items-center gap-2 min-w-0 flex-1 mr-2">
              {icon}
              <span
                className={`truncate ${
                  selected.length > 0 ? 'text-slate-200 font-mono' : 'text-slate-400'
                }`}
                title={selected.length > 0 ? selected.join(', ') : placeholder}
              >
                {selected.length === 0 ? placeholder : selected.join(', ')}
              </span>
              {selected.length > 1 && (
                <span className="bg-accent-950 text-accent-300 border border-accent-700/60 text-[10px] font-mono px-1.5 py-0.5 rounded-full font-semibold shrink-0">
                  {selected.length}
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              {selected.length > 0 && (
                <button
                  type="button"
                  onClick={handleClear}
                  className="text-slate-400 hover:text-slate-200 p-0.5 rounded hover:bg-dark-700 transition"
                  title="Clear selection"
                  aria-label="Clear selection"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
              <ChevronDown
                className={`w-3.5 h-3.5 text-slate-400 transition-transform duration-150 ${
                  isOpen ? 'rotate-180 text-accent-400' : ''
                }`}
              />
            </div>
          </>
        ) : (
          <>
            {icon}
            <span className="text-slate-400 font-medium text-[11px]">{label}:</span>
            <span
              className={`font-mono text-[11px] truncate max-w-[130px] ${
                selected.length > 0 ? 'text-accent-300 font-medium' : 'text-slate-400'
              }`}
              title={selected.length > 0 ? selected.join(', ') : placeholder}
            >
              {getSummaryText()}
            </span>

            {/* Selected Counter Badge */}
            {selected.length > 1 && (
              <span className="bg-accent-950 text-accent-300 border border-accent-700/60 text-[10px] font-mono px-1 rounded-full font-semibold">
                {selected.length}
              </span>
            )}

            {/* Clear selection 'x' button */}
            {selected.length > 0 && (
              <button
                type="button"
                onClick={handleClear}
                className="text-slate-500 hover:text-slate-200 p-0.5 rounded hover:bg-dark-800 transition ml-0.5"
                title={`Clear ${label.toLowerCase()} filter`}
                aria-label={`Clear ${label.toLowerCase()} filter`}
              >
                <X className="w-3 h-3" />
              </button>
            )}

            <ChevronDown
              className={`w-3 h-3 text-slate-400 transition-transform duration-150 ${
                isOpen ? 'rotate-180 text-accent-400' : ''
              }`}
            />
          </>
        )}
      </div>

      {/* Popover Dropdown Menu & Click-Away Interceptor */}
      {isOpen && (
        isForm && typeof document !== 'undefined' ? (
          createPortal(
            <>
              {/* Fixed transparent backdrop that captures click-aways to prevent triggering underlying elements */}
              <div
                data-testid="dropdown-backdrop"
                className="fixed inset-0 z-[9998] bg-transparent"
                onClick={(e) => {
                  e.stopPropagation();
                  setIsOpen(false);
                }}
              />

              <div
                ref={popoverRef}
                style={{
                  position: 'fixed',
                  top: coords ? `${coords.top}px` : '0px',
                  left: coords ? `${coords.left}px` : '0px',
                  width: coords ? `${coords.width}px` : '300px',
                  maxWidth: 'calc(100vw - 24px)',
                  visibility: coords ? 'visible' : 'hidden',
                }}
                className="bg-dark-900 border border-dark-700 rounded-md shadow-2xl z-[9999] py-1.5 animate-in fade-in-50 zoom-in-95 duration-100"
              >
                {renderDropdownMenu()}
              </div>
            </>,
            document.body
          )
        ) : (
          <>
            {/* Fixed transparent backdrop that captures click-aways to prevent triggering underlying elements */}
            <div
              data-testid="dropdown-backdrop"
              className="fixed inset-0 z-40 bg-transparent"
              onClick={(e) => {
                e.stopPropagation();
                setIsOpen(false);
              }}
            />

            <div
              ref={popoverRef}
              className="absolute left-0 mt-1 w-64 bg-dark-900 border border-dark-700 rounded-md shadow-2xl z-50 py-1.5 animate-in fade-in-50 zoom-in-95 duration-100"
            >
              {renderDropdownMenu()}
            </div>
          </>
        )
      )}
    </div>
  );
};
