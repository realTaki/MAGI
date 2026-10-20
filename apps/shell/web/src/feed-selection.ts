/** A checkbox selects a concrete displayed contract, never just a reusable name. */
export type FeedSelection = Record<string, string>;
export type FeedSourceChoice = { name: string; fingerprint: string };

export function initialFeedSelection(names: string[], sources: FeedSourceChoice[]): FeedSelection {
  return Object.fromEntries(sources.filter(source => names.includes(source.name)).map(source => [source.name, source.fingerprint]));
}

/** Refreshing cards/catalog is not approval of a changed or reconnected destination. */
export function reconcileFeedSelection(selected: FeedSelection, sources: FeedSourceChoice[]): FeedSelection {
  return Object.fromEntries(Object.entries(selected).filter(([name, fingerprint]) => sources.some(source => source.name === name && source.fingerprint === fingerprint)));
}

/** Only an explicit checkbox choice can bind a newly displayed contract. */
export function chooseFeedSource(selected: FeedSelection, source: FeedSourceChoice, checked: boolean): FeedSelection {
  const next = { ...selected };
  if (checked) next[source.name] = source.fingerprint; else delete next[source.name];
  return next;
}
