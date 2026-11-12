import { escapeRegExp } from '../demo/server.ts';

/**
 * The accessible name made of `parts`, as a pattern in which the space between two parts may be missing.
 *
 * jsdom reports a span as inline, and the name computation in the testing library then joins the text of
 * inline children without the spaces at their edges: "<span>Filters</span><span> 3</span>" is named
 * "Filters3" in the tests, while browsers keep the space and read "Filters 3". Pass the text of each
 * inline element as its own part, so the test checks the words and their order as a browser reads them.
 * Spaces inside a part must be there.
 */
export function spacedName(...parts: string[]): RegExp {
  return new RegExp(`^${parts.map(escapeRegExp).join('\\s*')}$`);
}
