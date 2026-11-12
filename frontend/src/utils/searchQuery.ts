import { LogEntry } from '../types.ts';

/** A search query as an expression: terms joined by AND, OR and NOT (`a NOT b` is a and not b). */
export type SearchNode =
  | { op: 'term'; term: string }
  | { op: 'and' | 'or' | 'not'; left: SearchNode; right: SearchNode };

export interface CompiledSearchQuery {
  /** Trimmed query, lower-cased */
  lowered: string;
  /** Words, phrases and column filters, without boolean keywords or brackets */
  terms: string[];
  /** The query as an expression, or null when its operators are incomplete (every term must match) */
  expr: SearchNode | null;
  /** True when the query uses AND, OR or NOT */
  hasOperators: boolean;
}

const SEARCH_OPERATORS = new Set(['and', 'or', 'not']);
const SEARCH_COLUMNS = new Set(['app_name', 'app', 'source_alias', 'source', 'host', 'message', 'msg']);
// The same tokens format_fts_query (backend/app/core/utils.py) reads: phrases, column filters, brackets, words
const SEARCH_TOKEN_RE = /("[^"]*"|'[^']*'|[a-z_]+:(?:"[^"]*"|[^\s()]+)|\(|\)|[^\s()]+)/g;

const isSearchOperator = (token: string | undefined) => token !== undefined && SEARCH_OPERATORS.has(token);

/**
 * True when history search would run the query as an expression. Like format_fts_query, it refuses
 * unbalanced quotes or brackets, an operator at either end, next to another or beside a bracket,
 * empty brackets, a term touching a bracket without an operator, and an empty column filter.
 */
function isValidSearchExpression(lowered: string, tokens: string[]): boolean {
  if (lowered.startsWith('*')) return false;
  if ((lowered.split('"').length - 1) % 2 !== 0 || (lowered.split("'").length - 1) % 2 !== 0) return false;
  let depth = 0;
  for (const char of lowered) {
    if (char === '(') depth++;
    else if (char === ')' && --depth < 0) return false;
  }
  if (depth !== 0) return false;

  const words = tokens.filter((t) => t !== '(' && t !== ')');
  if (words.length === 0 || isSearchOperator(words[0]) || isSearchOperator(words[words.length - 1])) return false;
  for (const word of words) {
    const colon = word.indexOf(':');
    if (colon > 0 && !word.startsWith('"') && !word.startsWith("'") && SEARCH_COLUMNS.has(word.slice(0, colon))) {
      const val = word.slice(colon + 1);
      if (!val || val === '*') return false;
    }
  }
  for (let i = 0; i < tokens.length - 1; i++) {
    const [t1, t2] = [tokens[i], tokens[i + 1]];
    if (isSearchOperator(t1) && isSearchOperator(t2)) return false;
    if (t1 === '(' && (isSearchOperator(t2) || t2 === ')')) return false;
    if (isSearchOperator(t1) && t2 === ')') return false;
    if (t2 === '(' && !isSearchOperator(t1) && t1 !== '(') return false;
    if (t1 === ')' && !isSearchOperator(t2) && t2 !== ')') return false;
  }
  return true;
}

/**
 * Parses validated tokens with the FTS5 precedence history search uses, tightest first: terms side
 * by side (an implicit AND), then NOT, then AND, then OR, each grouping left to right.
 */
function parseSearchTokens(tokens: string[]): SearchNode {
  let pos = 0;
  const parsePrimary = (): SearchNode => {
    if (tokens[pos] === '(') {
      pos++;
      const inner = parseOr();
      pos++; // the closing bracket
      return inner;
    }
    return { op: 'term', term: tokens[pos++] };
  };
  const parseAdjacent = (): SearchNode => {
    let node = parsePrimary();
    while (pos < tokens.length && tokens[pos] !== ')' && !isSearchOperator(tokens[pos])) {
      node = { op: 'and', left: node, right: parsePrimary() };
    }
    return node;
  };
  const parseBinary = (op: 'not' | 'and' | 'or', next: () => SearchNode) => (): SearchNode => {
    let node = next();
    while (tokens[pos] === op) {
      pos++;
      node = { op, left: node, right: next() };
    }
    return node;
  };
  const parseNot = parseBinary('not', parseAdjacent);
  const parseAnd = parseBinary('and', parseNot);
  const parseOr: () => SearchNode = parseBinary('or', parseAnd);
  return parseOr();
}

/** Splits a search query once, so each incoming live log is matched without re-parsing it. */
export function compileSearchQuery(query?: string): CompiledSearchQuery {
  const lowered = (query || '').trim().toLowerCase();
  const tokens = lowered.match(SEARCH_TOKEN_RE) || [];
  const terms = tokens.filter((t) => t !== '(' && t !== ')' && !isSearchOperator(t));
  const hasOperators = tokens.some(isSearchOperator);
  const expr = lowered && isValidSearchExpression(lowered, tokens) ? parseSearchTokens(tokens) : null;
  return { lowered, terms, expr, hasOperators };
}

// Word characters as the FTS5 unicode61 tokenizer of history search reads them: letters, digits and
// private use characters; everything else separates words, and accents are folded away
const SEARCH_WORD_RE = /[\p{L}\p{N}\p{Co}]+/gu;
const COMBINING_MARKS_RE = /\p{M}+/gu;

/** The words of a text as history search indexes them: lower-cased, accents removed. */
export function searchWords(text: string): string[] {
  return text.toLowerCase().normalize('NFD').replace(COMBINING_MARKS_RE, '').match(SEARCH_WORD_RE) || [];
}

/**
 * True when the words occur one after the other in fieldWords, as an FTS5 phrase: each word whole,
 * except that the last may be the start of a word when prefix is set (a term is sent as term*).
 */
function wordsOccur(fieldWords: string[], words: string[], prefix: boolean): boolean {
  const last = words.length - 1;
  for (let i = 0; i + last < fieldWords.length; i++) {
    let j = 0;
    while (j < last && fieldWords[i + j] === words[j]) j++;
    if (j === last && (prefix ? fieldWords[i + j].startsWith(words[j]) : fieldWords[i + j] === words[j])) {
      return true;
    }
  }
  return false;
}

// As in history search: a term shorter than this matches whole words unless it ends in *.
const MIN_AUTO_PREFIX_LENGTH = 3;

/**
 * A term's words, and whether its last word is a prefix: yes unless the term is a quoted phrase
 * or shorter than MIN_AUTO_PREFIX_LENGTH characters without a trailing *.
 */
function termWords(raw: string): { words: string[]; prefix: boolean } {
  const quoted = raw.length >= 2 && (raw[0] === '"' || raw[0] === "'") && raw[raw.length - 1] === raw[0];
  const prefix = !quoted && (raw.endsWith('*') || raw.length >= MIN_AUTO_PREFIX_LENGTH);
  return { words: searchWords(quoted ? raw.slice(1, -1) : raw), prefix };
}

/** The words of each searchable field of one log, split once per log and only when a term needs them. */
class LogWords {
  private cache = new Map<string, string[]>();
  constructor(private readonly log: LogEntry) {}
  get(field: 'message' | 'app_name' | 'source_alias' | 'source_ip'): string[] {
    let words = this.cache.get(field);
    if (!words) {
      words = searchWords(this.log[field] || '');
      this.cache.set(field, words);
    }
    return words;
  }
}

const FREE_TERM_FIELDS = ['message', 'app_name', 'source_alias', 'source_ip'] as const;

/**
 * One word, "phrase" or column filter (app:, host:, source:, msg: and their long forms) against a
 * log, matched like history search: at word starts, a phrase as consecutive words within one field.
 */
function matchesSearchTerm(term: string, logWords: LogWords): boolean {
  const colon = term.indexOf(':');
  if (colon > 0 && !term.startsWith('"') && !term.startsWith("'")) {
    const col = term.slice(0, colon);
    if (SEARCH_COLUMNS.has(col)) {
      const { words, prefix } = termWords(term.slice(colon + 1));
      if (words.length === 0) return true;
      if (col === 'app_name' || col === 'app') return wordsOccur(logWords.get('app_name'), words, prefix);
      if (col === 'message' || col === 'msg') return wordsOccur(logWords.get('message'), words, prefix);
      return (
        wordsOccur(logWords.get('source_alias'), words, prefix) ||
        wordsOccur(logWords.get('source_ip'), words, prefix)
      );
    }
  }

  const { words, prefix } = termWords(term);
  if (words.length === 0) return true;
  return FREE_TERM_FIELDS.some((field) => wordsOccur(logWords.get(field), words, prefix));
}

function evaluateSearchNode(node: SearchNode, matchTerm: (term: string) => boolean): boolean {
  switch (node.op) {
    case 'term':
      return matchTerm(node.term);
    case 'and':
      return evaluateSearchNode(node.left, matchTerm) && evaluateSearchNode(node.right, matchTerm);
    case 'or':
      return evaluateSearchNode(node.left, matchTerm) || evaluateSearchNode(node.right, matchTerm);
    case 'not':
      return evaluateSearchNode(node.left, matchTerm) && !evaluateSearchNode(node.right, matchTerm);
  }
}

export function matchesSearchQuery(
  log: LogEntry,
  query?: string | CompiledSearchQuery,
  precompiledRegex?: RegExp | null,
): boolean {
  const compiled = typeof query === 'string' || query === undefined ? compileSearchQuery(query) : query;
  if (!compiled.lowered) return true;

  // 1. A precompiled regex, when one is supplied, decides alone (the query is its source)
  if (precompiledRegex) {
    return [log.message, log.app_name, log.source_alias, log.source_ip].some((f) => precompiledRegex.test(f || ''));
  }

  // 2. The query as an expression (e.g. "error OR timeout", "auth NOT cron"), grouped like history search
  const logWords = new LogWords(log);
  const matchTerm = (term: string) => matchesSearchTerm(term, logWords);
  if (compiled.expr) {
    return evaluateSearchNode(compiled.expr, matchTerm);
  }

  // 3. Incomplete operators, as while typing ("error OR"): every remaining term must match
  return compiled.terms.length > 0 && compiled.terms.every(matchTerm);
}
