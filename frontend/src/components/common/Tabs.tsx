import React from 'react';

export interface TabItem<T extends string> {
  id: T;
  label: string;
  icon: React.ReactElement<{ className?: string }>;
  /** A count or status after the label. */
  extra?: React.ReactNode;
}

export interface TabsProps<T extends string> {
  /** Names the bar for screen readers ("Settings sections"). */
  label: string;
  items: TabItem<T>[];
  active: T;
  onSelect: (id: T) => void;
}

const TAB_BASE =
  'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition cursor-pointer shrink-0 border';
const TAB_ACTIVE = 'bg-raised text-accent-400 border-line-strong shadow-xs';
const TAB_IDLE = 'text-fg-muted hover:text-fg hover:bg-surface border-line hover:border-line-strong';

/**
 * A page's sub-tab bar. Each tab changes the page's path, so the bar is a navigation landmark and the
 * chosen tab is marked aria-current.
 */
export function Tabs<T extends string>({ label, items, active, onSelect }: TabsProps<T>) {
  return (
    <nav aria-label={label} className="flex flex-wrap items-center gap-2 border-b border-line pb-2">
      {items.map((item) => {
        const isActive = item.id === active;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onSelect(item.id)}
            aria-current={isActive ? 'page' : undefined}
            className={`${TAB_BASE} ${isActive ? TAB_ACTIVE : TAB_IDLE}`}
          >
            {React.cloneElement(item.icon, { className: 'w-3.5 h-3.5 shrink-0' })}
            <span>{item.label}</span>
            {item.extra}
          </button>
        );
      })}
    </nav>
  );
}
