/**
 * Field limits the backend enforces, shared by the forms that need them.
 * backend/tests/test_frontend_limits.py compares these with the backend models, so a change on
 * one side fails a test until the other side follows.
 */

/** Names of rules, saved views, API tokens and Docker hosts. */
export const NAME_MAX_LENGTH = 100;
/** A Docker host's alias (the Host / IP label on its logs). */
export const HOST_ALIAS_MAX_LENGTH = 64;
/** Drop rule host and app patterns. */
export const RULE_FILTER_MAX_LENGTH = 255;
/** Drop rule message patterns. */
export const MESSAGE_PATTERN_MAX_LENGTH = 1000;
export const PASSWORD_MIN_LENGTH = 8;
/** A follow-up question about an AI analysis. */
export const AI_FOLLOW_UP_MAX_LENGTH = 2000;
/** The situational context sent with an AI analysis. */
export const AI_USER_CONTEXT_MAX_LENGTH = 20000;
