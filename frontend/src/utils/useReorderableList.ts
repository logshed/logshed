import React, { useState } from 'react';

/**
 * Up and down buttons and drag-and-drop for a rule list. Each move hands the whole new order to
 * onReorder (usually the function from useSerialReorder); a drop on the dragged row itself does nothing.
 */
export function useReorderableList<T>(items: T[], onReorder: (newOrder: T[]) => void) {
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);

  const moveTo = (from: number, to: number) => {
    const newOrder = [...items];
    const [moved] = newOrder.splice(from, 1);
    newOrder.splice(to, 0, moved);
    onReorder(newOrder);
  };

  const moveItem = (index: number, direction: 'up' | 'down') => {
    const targetIndex = direction === 'up' ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= items.length) return;
    moveTo(index, targetIndex);
  };

  /** The drag props for the row at index. */
  const dragProps = (index: number) => ({
    draggable: true,
    onDragStart: (e: React.DragEvent<HTMLDivElement>) => {
      setDraggedIndex(index);
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', index.toString());
    },
    onDragOver: (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
    },
    onDrop: (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      const from = draggedIndex;
      setDraggedIndex(null);
      if (from === null || from === index) return;
      moveTo(from, index);
    },
    onDragEnd: () => setDraggedIndex(null),
  });

  return { draggedIndex, moveItem, dragProps };
}
