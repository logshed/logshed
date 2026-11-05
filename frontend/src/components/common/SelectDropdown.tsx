import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Check, ChevronDown, X } from 'lucide-react';
import { FILTER_CLEAR_BUTTON, FILTER_CLEAR_ROOM, POPOVER_PANEL, filterTriggerClass } from './dropdownStyles.ts';

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectDropdownProps {
  /** Shown before the value in the toolbar variant ("Severity:"); names the list in both. */
  label: string;
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  /** toolbar: a compact box with label and value; panel: a full-width field under a visible label. */
  variant?: 'toolbar' | 'panel';
  /** Highlights the box and value while the control filters. */
  active?: boolean;
  /** Shown as a clear button beside the trigger while active. */
  onClear?: () => void;
  clearLabel?: string;
  /** Panel variant: id of the visible label; the trigger is named by it followed by the value. */
  labelledBy?: string;
  /** The trigger's id, so a visible label can point at it. */
  id?: string;
  title?: string;
}

/** Nearest ancestor that scrolls vertically, whose bottom edge also limits room for the list. */
function scrollParentBounds(el: HTMLElement): { top: number; bottom: number } {
  let top = 0;
  let bottom = window.innerHeight;
  for (let node = el.parentElement; node; node = node.parentElement) {
    const overflowY = window.getComputedStyle(node).overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') {
      const rect = node.getBoundingClientRect();
      top = Math.max(top, rect.top);
      bottom = Math.min(bottom, rect.bottom);
      break;
    }
  }
  return { top, bottom };
}

/**
 * A single-choice dropdown in LogSearchBar's look, used where a native select would show the
 * operating system's list. Keys follow a native select: Enter, Space, Down, Up or Alt+Down open the
 * list on the chosen row; Up, Down, Home, End and a typed letter move; Enter or Space chooses; Esc and
 * Tab close it, and focus goes back to the trigger.
 */
export const SelectDropdown: React.FC<SelectDropdownProps> = ({
  label,
  options,
  value,
  onChange,
  variant = 'toolbar',
  active = false,
  onClear,
  clearLabel,
  labelledBy,
  id,
  title,
}) => {
  const baseId = useId();
  const labelId = `${baseId}-label`;
  const valueId = `${baseId}-value`;
  const listId = `${baseId}-list`;
  const optionId = (index: number) => `${baseId}-option-${index}`;

  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [placeAbove, setPlaceAbove] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const isPanel = variant === 'panel';
  const selectedIndex = options.findIndex((o) => o.value === value);
  const selectedLabel = selectedIndex >= 0 ? options[selectedIndex].label : value;
  const showClear = active && Boolean(onClear);

  const open = () => {
    setActiveIndex(Math.max(0, selectedIndex));
    setPlaceAbove(false);
    setIsOpen(true);
  };

  const close = () => {
    setIsOpen(false);
    triggerRef.current?.focus();
  };

  const choose = (index: number) => {
    const option = options[index];
    if (option && option.value !== value) onChange(option.value);
    close();
  };

  // On open: focus the list, and open it upwards when there is no room below but room above
  useLayoutEffect(() => {
    if (!isOpen || !listRef.current || !triggerRef.current) return;
    listRef.current.focus();
    const rect = triggerRef.current.getBoundingClientRect();
    const height = listRef.current.offsetHeight;
    const bounds = scrollParentBounds(triggerRef.current);
    if (rect.bottom + height + 4 > bounds.bottom && rect.top - height - 4 >= bounds.top) setPlaceAbove(true);
  }, [isOpen]);

  // Keep the active row in view
  useEffect(() => {
    if (!isOpen) return;
    const row = document.getElementById(`${baseId}-option-${activeIndex}`);
    row?.scrollIntoView?.({ block: 'nearest' });
  }, [isOpen, activeIndex, baseId]);

  // Esc in the capture phase, so it closes the list and never a dialog around it
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        e.preventDefault();
        setIsOpen(false);
        triggerRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [isOpen]);

  const handleTriggerKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    // Handled here rather than through the button's click, which preventDefault holds back
    if (!isOpen && ['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
      e.preventDefault();
      open();
    }
  };

  const handleListKeyDown = (e: React.KeyboardEvent<HTMLUListElement>) => {
    const last = options.length - 1;
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActiveIndex((i) => Math.min(last, i + 1));
        return;
      case 'ArrowUp':
        e.preventDefault();
        setActiveIndex((i) => Math.max(0, i - 1));
        return;
      case 'Home':
      case 'PageUp':
        e.preventDefault();
        setActiveIndex(0);
        return;
      case 'End':
      case 'PageDown':
        e.preventDefault();
        setActiveIndex(last);
        return;
      case 'Enter':
      case ' ':
        e.preventDefault();
        choose(activeIndex);
        return;
      case 'Tab':
        // Closes without choosing; focus is back on the trigger before the browser moves it on
        setIsOpen(false);
        triggerRef.current?.focus();
        return;
    }
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const letter = e.key.toLowerCase();
      for (let step = 1; step <= options.length; step++) {
        const index = (activeIndex + step) % options.length;
        if (options[index].label.toLowerCase().startsWith(letter)) {
          setActiveIndex(index);
          break;
        }
      }
    }
  };

  const triggerClass = isPanel
    ? `w-full flex items-center justify-between gap-2 bg-dark-950 border rounded px-3 py-2 text-xs font-mono cursor-pointer select-none transition focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500/20 ${
        isOpen
          ? 'border-accent-500 ring-1 ring-accent-500/20'
          : active
          ? 'border-accent-600/60 text-accent-300 font-medium'
          : 'border-dark-700 text-slate-400'
      }`
    : filterTriggerClass(isOpen, active);

  const chevron = (
    <ChevronDown
      aria-hidden="true"
      className={`${isPanel ? 'w-3.5 h-3.5' : 'w-3 h-3'} text-slate-400 shrink-0 transition-transform duration-150 ${
        isOpen ? 'rotate-180 text-accent-400' : ''
      }`}
    />
  );

  const clearButton = showClear && (
    <button
      type="button"
      onClick={onClear}
      className={
        isPanel
          ? 'text-slate-500 hover:text-slate-200 rounded hover:bg-dark-800 transition cursor-pointer shrink-0 p-1.5'
          : FILTER_CLEAR_BUTTON
      }
      title={clearLabel}
      aria-label={clearLabel}
    >
      <X className={isPanel ? 'w-4 h-4' : 'w-3 h-3'} />
    </button>
  );

  const list = isOpen && (
    <>
      {/* Transparent backdrop: a click outside closes the list without reaching what is under it */}
      <div
        data-testid="select-dropdown-backdrop"
        className="fixed inset-0 z-40 bg-transparent"
        onClick={(e) => {
          e.stopPropagation();
          setIsOpen(false);
        }}
      />
      <ul
        ref={listRef}
        id={listId}
        role="listbox"
        tabIndex={-1}
        aria-labelledby={labelledBy ?? labelId}
        aria-activedescendant={optionId(activeIndex)}
        onKeyDown={handleListKeyDown}
        className={`absolute left-0 ${placeAbove ? 'bottom-full mb-1' : 'top-full mt-1'} ${
          isPanel ? 'w-full' : 'min-w-full w-max'
        } max-h-72 overflow-y-auto z-50 outline-none ${POPOVER_PANEL}`}
      >
        {/* The list keeps focus; the active row is marked by its ring (aria-activedescendant) */}
        {options.map((option, index) => {
          const isChosen = index === selectedIndex;
          const isActive = index === activeIndex;
          return (
            <li
              key={option.value}
              id={optionId(index)}
              role="option"
              aria-selected={isChosen}
              onClick={() => choose(index)}
              onMouseMove={() => setActiveIndex(index)}
              className={`flex items-center justify-between gap-3 cursor-pointer select-none font-mono ${
                isPanel ? 'px-3 py-2 text-xs' : 'px-2.5 py-1 text-[11px]'
              } ${isActive ? 'bg-dark-800 ring-1 ring-inset ring-accent-500/60' : ''} ${
                isChosen ? 'text-accent-200 font-medium' : isActive ? 'text-slate-100' : 'text-slate-300'
              }`}
            >
              <span className="whitespace-nowrap">{option.label}</span>
              <Check aria-hidden="true" className={`w-3 h-3 shrink-0 ${isChosen ? 'text-accent-400' : 'invisible'}`} />
            </li>
          );
        })}
      </ul>
    </>
  );

  const trigger = (
    <button
      ref={triggerRef}
      id={id}
      type="button"
      onClick={() => (isOpen ? setIsOpen(false) : open())}
      onKeyDown={handleTriggerKeyDown}
      aria-haspopup="listbox"
      aria-expanded={isOpen}
      aria-controls={isOpen ? listId : undefined}
      aria-labelledby={`${labelledBy ?? labelId} ${valueId}`}
      title={title}
      className={triggerClass}
    >
      {isPanel ? (
        <>
          <span id={valueId} className="truncate">{selectedLabel}</span>
          {chevron}
        </>
      ) : (
        <>
          <span id={labelId} className="text-slate-400 font-medium text-[11px]">{label}:</span>
          <span
            id={valueId}
            className={`font-mono text-[11px] whitespace-nowrap ${active ? 'text-accent-300 font-medium' : 'text-slate-400'}`}
          >
            {selectedLabel}
          </span>
          {showClear && <span className={FILTER_CLEAR_ROOM} aria-hidden="true" />}
          {chevron}
        </>
      )}
    </button>
  );

  if (isPanel) {
    return (
      <div data-active={active} className={`flex items-center gap-2 ${active ? 'text-slate-100' : ''}`}>
        <div className="relative flex-1 min-w-0">
          {trigger}
          {list}
        </div>
        {clearButton}
      </div>
    );
  }

  return (
    <div data-active={active} className="relative inline-block text-left">
      {trigger}
      {clearButton}
      {list}
    </div>
  );
};
