import { stripAnsi } from './formatters.ts';

/** Messages longer than this are not parsed: the drawer shows them as text. */
export const JSON_MESSAGE_MAX_LENGTH = 256 * 1024;

/** Most characters before the opening brace, for a level, a timestamp or a logger name. */
export const JSON_PREFIX_MAX_LENGTH = 80;

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export interface JsonMessage {
  /** Text before the object (a timestamp or level), trimmed; empty when the message starts with it. */
  prefix: string;
  value: JsonObject;
}

/**
 * The JSON object a log message holds, or null. The message (colour codes removed, trimmed) must be
 * one JSON object, alone or after a short prefix of up to JSON_PREFIX_MAX_LENGTH characters, with
 * nothing after it. Arrays, strings and numbers on their own are left as text.
 */
export function parseJsonMessage(message: string | null | undefined): JsonMessage | null {
  if (!message || message.length > JSON_MESSAGE_MAX_LENGTH) return null;
  const text = stripAnsi(message).trim();
  if (!text.endsWith('}')) return null;
  const start = text.indexOf('{');
  if (start < 0 || start > JSON_PREFIX_MAX_LENGTH) return null;
  let value: unknown;
  try {
    value = JSON.parse(text.slice(start));
  } catch {
    // Not JSON, or nested past the engine's limit (a RangeError)
    return null;
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  return { prefix: text.slice(0, start).trim(), value: value as JsonObject };
}

/** The text a value's copy button copies: a string as it is, anything else as JSON. */
export function jsonValueToCopy(value: JsonValue): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}
