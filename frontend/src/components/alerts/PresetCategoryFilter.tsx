import React, { useEffect, useMemo, useState } from 'react';
import { SelectDropdown } from '../common/SelectDropdown.tsx';

/** The built-in categories, in the order the dialogs list them; any other category follows, then "Other". */
const CATEGORY_ORDER = [
  'Security',
  'Storage and disks',
  'Backups',
  'Power',
  'Network',
  'VMs and containers',
  'System',
  'Apps',
];

/** Custom presets may leave the category out. */
const OTHER = 'Other';
const ALL = '';

export function presetCategory(preset: { category?: string | null }): string {
  return preset.category?.trim() || OTHER;
}

function categoryRank(category: string): number {
  const index = CATEGORY_ORDER.indexOf(category);
  if (index >= 0) return index;
  return category === OTHER ? CATEGORY_ORDER.length + 1 : CATEGORY_ORDER.length;
}

/**
 * The presets of the chosen category (all of them by default), sorted by category so related presets
 * sit together, and the filter that chooses it. The choice goes back to all categories when the
 * dialog closes.
 */
export function usePresetCategoryFilter<T extends { category?: string | null }>(presets: T[], isOpen: boolean) {
  const [category, setCategory] = useState(ALL);

  useEffect(() => {
    if (!isOpen) setCategory(ALL);
  }, [isOpen]);

  const sorted = useMemo(
    () =>
      presets
        .map((preset, index) => ({ preset, index, category: presetCategory(preset) }))
        .sort(
          (a, b) =>
            categoryRank(a.category) - categoryRank(b.category) ||
            a.category.localeCompare(b.category) ||
            a.index - b.index
        ),
    [presets]
  );

  const options = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of sorted) counts.set(item.category, (counts.get(item.category) ?? 0) + 1);
    return [
      { value: ALL, label: `All categories (${presets.length})` },
      ...[...counts].map(([name, count]) => ({ value: name, label: `${name} (${count})` })),
    ];
  }, [sorted, presets.length]);

  // A category can disappear while chosen (a custom preset file removed): show everything again
  const chosen = category !== ALL && options.some((o) => o.value === category) ? category : ALL;
  const visible = sorted.filter((item) => chosen === ALL || item.category === chosen).map((item) => item.preset);

  return { category: chosen, setCategory, options, visible };
}

export interface PresetCategoryFilterProps {
  /** Prefix for the ids of the field and its label, unique in the page. */
  idPrefix: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}

/** The category field shown above the preset cards. */
export const PresetCategoryFilter: React.FC<PresetCategoryFilterProps> = ({ idPrefix, value, options, onChange }) => (
  <div className="flex items-center gap-2 shrink-0">
    <label id={`${idPrefix}-category-label`} htmlFor={`${idPrefix}-category`} className="text-xs text-slate-400 w-24 sm:w-auto">
      Category:
    </label>
    <div className="w-52">
      <SelectDropdown
        variant="panel"
        fieldClassName="bg-dark-800 border-dark-700 rounded-lg px-2.5 py-1.5 text-slate-200"
        id={`${idPrefix}-category`}
        labelledBy={`${idPrefix}-category-label`}
        label="Category"
        options={options}
        value={value}
        onChange={onChange}
      />
    </div>
  </div>
);
