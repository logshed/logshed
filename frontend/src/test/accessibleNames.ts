/**
 * The accessible name `text` as a pattern in which each space may be missing.
 *
 * jsdom reports a span as inline, and the name computation in the testing library then joins the text of
 * inline children without the spaces at their edges: "<span>Filters</span><span> 3</span>" is named
 * "Filters3" in the tests, while browsers keep the space and read "Filters 3". Use this for names built
 * from several inline elements, so the test checks the words and their order as a browser reads them.
 */
export function spacedName(text: string): RegExp {
  const escaped = text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^${escaped.replace(/ /g, '\\s*')}$`);
}
