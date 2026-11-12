// Recharts reads some parts of a chart, such as the Cells that fill each bar, through react-is,
// which must match the React version: an older react-is does not know React 19 fragments, and
// Cells wrapped in one are then ignored.
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { BarChart, Bar, Cell, XAxis } from 'recharts';

describe('Recharts with React fragments', () => {
  it('colours bars with Cells wrapped in a fragment', () => {
    const data = [{ t: 'a', v: 2 }, { t: 'b', v: 5 }];
    const { container } = render(
      <BarChart width={300} height={150} data={data}>
        <XAxis dataKey="t" />
        <Bar dataKey="v" isAnimationActive={false}>
          <>
            <Cell fill="#123456" />
            <Cell fill="#654321" />
          </>
        </Bar>
      </BarChart>
    );
    const fills = [...container.querySelectorAll('.recharts-bar-rectangle path')].map((path) => path.getAttribute('fill'));
    expect(fills).toEqual(['#123456', '#654321']);
  });
});
