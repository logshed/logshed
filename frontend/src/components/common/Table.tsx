import React from 'react';

/** The heading row of every table: small upper-case sans text on a slightly darker strip. */
export const TABLE_HEAD_ROW = 'border-b border-line/60 bg-canvas/40 text-[11px] font-semibold text-fg-muted uppercase tracking-wider';
export const TABLE_HEAD_CELL = 'py-2.5 px-4 font-semibold';
const ALIGN = { left: 'text-left', right: 'text-right', center: 'text-center' } as const;

export interface TableColumn {
  label: React.ReactNode;
  align?: keyof typeof ALIGN;
  /** Extra classes of the cell, such as a width or a breakpoint that hides it. */
  className?: string;
}

/** A table's thead in the shared heading style. Grids built from divs use the two class constants. */
export const TableHead: React.FC<{ columns: TableColumn[] }> = ({ columns }) => (
  <thead>
    <tr className={TABLE_HEAD_ROW}>
      {columns.map((column, i) => (
        <th
          key={i}
          scope="col"
          className={`${TABLE_HEAD_CELL} ${ALIGN[column.align ?? 'left']}${column.className ? ` ${column.className}` : ''}`}
        >
          {column.label}
        </th>
      ))}
    </tr>
  </thead>
);
