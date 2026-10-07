import React from 'react';
import { Copy, Check } from 'lucide-react';
import { useClipboard } from '../../utils/hooks.ts';

interface CodeBlockProps {
  language?: string;
  code: string;
}

export const CodeBlock: React.FC<CodeBlockProps> = ({ language, code }) => {
  const { copied, copy } = useClipboard();

  const handleCopy = () => {
    copy(code);
  };

  return (
    <div className="my-2 rounded-lg border border-dark-700 bg-dark-900 overflow-hidden shadow-xs">
      <div className="flex items-center justify-between px-3 py-1 bg-dark-950 border-b border-dark-800 text-[11px] font-mono text-slate-400">
        <span className="uppercase tracking-wider text-[10px] font-semibold text-slate-400">
          {language || 'code'}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1 hover:text-slate-200 transition text-[11px] px-1.5 py-0.5 rounded hover:bg-dark-800 cursor-pointer"
          title="Copy code"
        >
          {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <pre className="p-3 overflow-x-auto text-xs font-mono text-slate-200 leading-relaxed select-text">
        <code>{code}</code>
      </pre>
    </div>
  );
};

/**
 * Parses inline Markdown tokens: `code`, **bold**, __bold__, *italic*, _italic_, [link](url)
 * Escapes raw HTML through React element mapping to ensure XSS safety.
 */
export function renderInline(text: string): React.ReactNode[] {
  if (!text) return [];

  const nodes: React.ReactNode[] = [];
  let remaining = text;
  let keyIndex = 0;

  // Match earliest inline markdown token
  const inlineRegex = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|\[[^\]]+\]\([^)]+\))/;

  while (remaining.length > 0) {
    const match = remaining.match(inlineRegex);
    if (!match || match.index === undefined) {
      nodes.push(remaining);
      break;
    }

    // Add preceding plain text
    if (match.index > 0) {
      nodes.push(remaining.substring(0, match.index));
    }

    const matchedToken = match[0];
    const tokenKey = `inline-${keyIndex++}`;

    if (matchedToken.startsWith('`') && matchedToken.endsWith('`')) {
      const codeContent = matchedToken.slice(1, -1);
      nodes.push(
        <code
          key={tokenKey}
          className="bg-dark-900 text-accent-300 px-1.5 py-0.5 rounded font-mono text-[11px] border border-dark-700 select-text"
        >
          {codeContent}
        </code>
      );
    } else if (
      (matchedToken.startsWith('**') && matchedToken.endsWith('**')) ||
      (matchedToken.startsWith('__') && matchedToken.endsWith('__'))
    ) {
      const boldContent = matchedToken.slice(2, -2);
      nodes.push(
        <strong key={tokenKey} className="font-semibold text-slate-100">
          {renderInline(boldContent)}
        </strong>
      );
    } else if (
      (matchedToken.startsWith('*') && matchedToken.endsWith('*')) ||
      (matchedToken.startsWith('_') && matchedToken.endsWith('_'))
    ) {
      const italicContent = matchedToken.slice(1, -1);
      nodes.push(
        <em key={tokenKey} className="italic text-slate-300">
          {renderInline(italicContent)}
        </em>
      );
    } else if (matchedToken.startsWith('[') && matchedToken.includes('](') && matchedToken.endsWith(')')) {
      const linkText = matchedToken.substring(1, matchedToken.indexOf(']('));
      const linkUrl = matchedToken.substring(matchedToken.indexOf('](') + 2, matchedToken.length - 1);
      const trimmedUrl = linkUrl.trim();
      const isSafeProtocol = /^(https?:\/\/|mailto:|#)/i.test(trimmedUrl);
      const safeHref = isSafeProtocol ? trimmedUrl : '#';
      nodes.push(
        <a
          key={tokenKey}
          href={safeHref}
          target={safeHref === '#' ? undefined : '_blank'}
          rel="noopener noreferrer"
          className="text-accent-400 hover:underline"
        >
          {renderInline(linkText)}
        </a>
      );
    } else {
      nodes.push(matchedToken);
    }

    remaining = remaining.substring(match.index + matchedToken.length);
  }

  return nodes;
}

export type BlockNode =
  | { type: 'code_block'; language: string; code: string }
  | { type: 'heading'; level: number; text: string }
  | { type: 'ordered_list'; items: Array<{ num: string; blocks: BlockNode[] }> }
  | { type: 'unordered_list'; items: Array<{ blocks: BlockNode[] }> }
  | { type: 'paragraph'; lines: string[] };

/**
 * Strips common leading whitespace prefix from all non-empty lines (similar to Python textwrap.dedent).
 * Preserves relative indentation within the block.
 */
export function dedentLines(lines: string[]): string[] {
  const nonEmptyLines = lines.filter((l) => l.trim() !== '');
  if (nonEmptyLines.length === 0) return lines;

  let minIndent = Infinity;
  for (const line of nonEmptyLines) {
    const match = line.match(/^[ \t]*/);
    const indent = match ? match[0].length : 0;
    if (indent < minIndent) {
      minIndent = indent;
    }
  }

  if (minIndent === 0 || minIndent === Infinity) {
    return lines;
  }

  return lines.map((line) => {
    if (line.trim() === '') return '';
    return line.length >= minIndent ? line.slice(minIndent) : line;
  });
}

/**
 * Parses markdown into structured Block AST.
 * Handles nested code blocks in list items, preserving code syntax (including YAML dashes).
 */
export function parseMarkdownBlocks(content: string): BlockNode[] {
  if (!content) return [];
  const lines = content.split(/\r?\n/);
  const blocks: BlockNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // 1. Empty lines
    if (line.trim() === '') {
      i++;
      continue;
    }

    // 2. Fenced code block at top level
    const codeMatch = line.match(/^\s*```([a-zA-Z0-9_-]*)/);
    if (codeMatch) {
      const language = codeMatch[1] || '';
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].match(/^\s*```/)) {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // skip closing ```
      const dedented = dedentLines(codeLines);
      blocks.push({
        type: 'code_block',
        language,
        code: dedented.join('\n'),
      });
      continue;
    }

    // 3. Headings (#, ##, ###)
    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      blocks.push({
        type: 'heading',
        level: headingMatch[1].length,
        text: headingMatch[2],
      });
      i++;
      continue;
    }

    // 4. Ordered list item (e.g., "1. ...", "2. ...")
    const orderedMatch = line.match(/^\s*(\d+)\.\s+(.*)$/);
    if (orderedMatch) {
      const items: Array<{ num: string; blocks: BlockNode[] }> = [];

      while (i < lines.length) {
        const itemMatch = lines[i].match(/^\s*(\d+)\.\s+(.*)$/);
        if (!itemMatch) break;

        const num = itemMatch[1];
        const rawFirstLine = itemMatch[2];
        const rawLines: string[] = [rawFirstLine];
        i++;

        // Collect all subsequent lines belonging to this list item
        while (i < lines.length) {
          // If we encounter a fenced code block inside this list item, consume entire code block verbatim
          if (lines[i].match(/^\s*```/)) {
            rawLines.push(lines[i]);
            i++;
            while (i < lines.length && !lines[i].match(/^\s*```/)) {
              rawLines.push(lines[i]);
              i++;
            }
            if (i < lines.length) {
              rawLines.push(lines[i]);
              i++;
            }
            continue;
          }

          // If blank line, look ahead
          if (lines[i].trim() === '') {
            let j = i + 1;
            while (j < lines.length && lines[j].trim() === '') j++;
            if (j < lines.length) {
              // Next line starts a new list item or heading -> current item is finished
              if (
                lines[j].match(/^\s*\d+\.\s+/) ||
                lines[j].match(/^\s*[-*+]\s+/) ||
                lines[j].match(/^#{1,6}\s+/)
              ) {
                break;
              }
              // Next line is indented or code fence -> belongs to current item
              if (lines[j].startsWith(' ') || lines[j].startsWith('\t') || lines[j].match(/^\s*```/)) {
                rawLines.push('');
                i++;
                continue;
              }
            }
            break;
          }

          // If next line starts a new list item or heading
          if (
            lines[i].match(/^\s*\d+\.\s+/) ||
            lines[i].match(/^\s*[-*+]\s+/) ||
            lines[i].match(/^#{1,6}\s+/)
          ) {
            break;
          }

          rawLines.push(lines[i]);
          i++;
        }

        // Parse list item's internal content into blocks
        const itemContent = rawLines.join('\n');
        const itemBlocks = parseMarkdownBlocks(itemContent);
        items.push({ num, blocks: itemBlocks });
      }

      blocks.push({ type: 'ordered_list', items });
      continue;
    }

    // 5. Unordered list item (e.g., "- ...", "* ...")
    const unorderedMatch = line.match(/^\s*[-*+]\s+(.*)$/);
    if (unorderedMatch) {
      const items: Array<{ blocks: BlockNode[] }> = [];

      while (i < lines.length) {
        const itemMatch = lines[i].match(/^\s*[-*+]\s+(.*)$/);
        if (!itemMatch) break;

        const rawFirstLine = itemMatch[1];
        const rawLines: string[] = [rawFirstLine];
        i++;

        while (i < lines.length) {
          if (lines[i].match(/^\s*```/)) {
            rawLines.push(lines[i]);
            i++;
            while (i < lines.length && !lines[i].match(/^\s*```/)) {
              rawLines.push(lines[i]);
              i++;
            }
            if (i < lines.length) {
              rawLines.push(lines[i]);
              i++;
            }
            continue;
          }

          if (lines[i].trim() === '') {
            let j = i + 1;
            while (j < lines.length && lines[j].trim() === '') j++;
            if (j < lines.length) {
              if (
                lines[j].match(/^\s*\d+\.\s+/) ||
                lines[j].match(/^\s*[-*+]\s+/) ||
                lines[j].match(/^#{1,6}\s+/)
              ) {
                break;
              }
              if (lines[j].startsWith(' ') || lines[j].startsWith('\t') || lines[j].match(/^\s*```/)) {
                rawLines.push('');
                i++;
                continue;
              }
            }
            break;
          }

          if (
            lines[i].match(/^\s*\d+\.\s+/) ||
            lines[i].match(/^\s*[-*+]\s+/) ||
            lines[i].match(/^#{1,6}\s+/)
          ) {
            break;
          }

          rawLines.push(lines[i]);
          i++;
        }

        const itemContent = rawLines.join('\n');
        const itemBlocks = parseMarkdownBlocks(itemContent);
        items.push({ blocks: itemBlocks });
      }

      blocks.push({ type: 'unordered_list', items });
      continue;
    }

    // 6. Regular paragraph
    const pLines: string[] = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !lines[i].match(/^\s*```/) &&
      !lines[i].match(/^#{1,6}\s+/) &&
      !lines[i].match(/^\s*\d+\.\s+/) &&
      !lines[i].match(/^\s*[-*+]\s+/)
    ) {
      pLines.push(lines[i]);
      i++;
    }

    blocks.push({
      type: 'paragraph',
      lines: pLines,
    });
  }

  return blocks;
}

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content, className = '' }) => {
  if (!content) return null;

  const blocks = parseMarkdownBlocks(content);

  const renderBlock = (block: BlockNode, index: number): React.ReactNode => {
    switch (block.type) {
      case 'code_block':
        return <CodeBlock key={`block-${index}`} language={block.language} code={block.code} />;

      case 'heading': {
        const headingClass =
          block.level === 1
            ? 'text-sm font-bold text-slate-100 mt-3 mb-1.5'
            : block.level === 2
            ? 'text-xs font-bold text-slate-100 mt-2.5 mb-1'
            : 'text-[11px] font-semibold text-slate-200 mt-2 mb-1 uppercase tracking-wider';
        return (
          <div key={`heading-${index}`} className={headingClass}>
            {renderInline(block.text)}
          </div>
        );
      }

      case 'ordered_list':
        return (
          <div key={`ol-${index}`} className="space-y-2.5 my-2">
            {block.items.map((item, itemIdx) => (
              <div key={`item-${itemIdx}`} className="flex items-start gap-2.5">
                <span className="shrink-0 flex items-center justify-center w-5 h-5 rounded-full bg-dark-800 border border-dark-700 text-accent-400 font-mono text-[11px] font-semibold mt-0.5">
                  {item.num}
                </span>
                <div className="flex-1 min-w-0 text-slate-200 text-xs leading-relaxed space-y-1">
                  {item.blocks.map((subBlock, subIdx) => renderBlock(subBlock, subIdx))}
                </div>
              </div>
            ))}
          </div>
        );

      case 'unordered_list':
        return (
          <div key={`ul-${index}`} className="space-y-1 my-1.5">
            {block.items.map((item, itemIdx) => (
              <div key={`uitem-${itemIdx}`} className="flex items-start gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-accent-400 shrink-0 mt-1.5" />
                <div className="flex-1 min-w-0 text-slate-200 text-xs leading-relaxed space-y-1">
                  {item.blocks.map((subBlock, subIdx) => renderBlock(subBlock, subIdx))}
                </div>
              </div>
            ))}
          </div>
        );

      case 'paragraph':
        return (
          <p key={`para-${index}`} className="text-slate-200 text-xs leading-relaxed my-1">
            {block.lines.map((pLine, lineIdx) => (
              <React.Fragment key={lineIdx}>
                {lineIdx > 0 && ' '}
                {renderInline(pLine)}
              </React.Fragment>
            ))}
          </p>
        );

      default:
        return null;
    }
  };

  return <div className={`space-y-1 ${className}`}>{blocks.map((block, idx) => renderBlock(block, idx))}</div>;
};
