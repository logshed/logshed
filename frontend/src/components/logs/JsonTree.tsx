import React, { useState } from 'react';
import { DisclosureButton } from '../common/Button.tsx';
import { CopyButton } from '../common/CopyButton.tsx';
import { JsonObject, JsonValue, jsonValueToCopy } from '../../utils/jsonMessage.ts';

/** Two levels of keys show at first: the message's own and those of the objects in it; deeper ones start closed. */
const OPEN_DEPTH = 1;

/** Most entries listed per object or array; the rest are in the message text. */
export const JSON_TREE_MAX_ENTRIES = 200;

type Container = JsonObject | JsonValue[];

function isContainer(value: JsonValue): value is Container {
  return value !== null && typeof value === 'object';
}

function entriesOf(value: Container): [string, JsonValue][] {
  return Array.isArray(value) ? value.map((v, i) => [String(i), v]) : Object.entries(value);
}

function childPath(path: string, key: string, parentIsArray: boolean): string {
  if (parentIsArray) return `${path}[${key}]`;
  return path ? `${path}.${key}` : key;
}

function summary(value: Container): string {
  const count = Array.isArray(value) ? value.length : Object.keys(value).length;
  if (Array.isArray(value)) return count === 1 ? '1 item' : `${count} items`;
  return count === 1 ? '1 key' : `${count} keys`;
}

const Leaf: React.FC<{ value: Exclude<JsonValue, Container> }> = ({ value }) => {
  if (typeof value === 'string') return <span className="text-success-300">&quot;{value}&quot;</span>;
  if (typeof value === 'number') return <span className="text-warning-300">{String(value)}</span>;
  return <span className="text-highlight-300">{String(value)}</span>;
};

interface NodeProps {
  name: string;
  /** The key's place in the object, as `a.b[2].c`, for the copy button's name. */
  path: string;
  value: JsonValue;
  /** Array items show their index in a quieter colour than object keys. */
  isIndex: boolean;
  depth: number;
}

const JsonNode: React.FC<NodeProps> = ({ name, path, value, isIndex, depth }) => {
  const [open, setOpen] = useState(depth < OPEN_DEPTH);
  const keyLabel = <span className={isIndex ? 'text-fg-muted' : 'text-accent-300'}>{name}</span>;

  if (!isContainer(value)) {
    return (
      <li className="flex items-start gap-1 pl-5">
        <span className="min-w-0 flex-1 whitespace-pre-wrap break-all py-0.5">
          {keyLabel}
          <span className="text-fg-muted">: </span>
          <Leaf value={value} />
        </span>
        <CopyButton variant="icon" text={jsonValueToCopy(value)} label={`Copy value of ${path}`} iconClassName="w-3 h-3" />
      </li>
    );
  }

  const entries = entriesOf(value);
  const shown = entries.slice(0, JSON_TREE_MAX_ENTRIES);
  const hidden = entries.length - shown.length;
  const brackets = Array.isArray(value) ? ['[', ']'] : ['{', '}'];

  return (
    <li>
      <div className="flex items-start gap-1">
        <DisclosureButton open={open} onToggle={() => setOpen((o) => !o)} className="flex-1">
          <span className="min-w-0 break-all">
            {keyLabel}
            <span className="text-fg-muted">
              : {brackets[0]}
              {open ? '' : ` ${summary(value)} ${brackets[1]}`}
            </span>
          </span>
        </DisclosureButton>
        <CopyButton variant="icon" text={jsonValueToCopy(value)} label={`Copy value of ${path}`} iconClassName="w-3 h-3" />
      </div>
      {open && (
        <>
          <ul className="ml-2 border-l border-line pl-2">
            {shown.map(([k, v]) => (
              <JsonNode
                key={k}
                name={k}
                path={childPath(path, k, Array.isArray(value))}
                value={v}
                isIndex={Array.isArray(value)}
                depth={depth + 1}
              />
            ))}
            {hidden > 0 && (
              <li className="pl-5 py-0.5 italic text-fg-subtle">
                {hidden.toLocaleString()} more not shown (see the text view)
              </li>
            )}
          </ul>
          <div className="pl-5 text-fg-muted">{brackets[1]}</div>
        </>
      )}
    </li>
  );
};

/**
 * A JSON log message as a tree of keys and values: objects and arrays open and close, and each
 * value has a copy button. Two levels of keys show at first. Display only: the stored message is unchanged.
 */
export const JsonTree: React.FC<{ value: JsonObject; label: string }> = ({ value, label }) => {
  const entries = entriesOf(value);
  const shown = entries.slice(0, JSON_TREE_MAX_ENTRIES);
  const hidden = entries.length - shown.length;
  if (entries.length === 0) return <div className="font-mono text-xs text-fg-muted">{'{}'}</div>;
  return (
    <ul aria-label={label} className="font-mono text-xs text-fg">
      {shown.map(([k, v]) => (
        <JsonNode key={k} name={k} path={k} value={v} isIndex={false} depth={0} />
      ))}
      {hidden > 0 && (
        <li className="pl-5 py-0.5 italic text-fg-subtle">{hidden.toLocaleString()} more not shown (see the text view)</li>
      )}
    </ul>
  );
};
