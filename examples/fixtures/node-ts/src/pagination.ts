/** Paging helpers. */

/**
 * SPEC: how many pages are needed to show `total` items `size` at a time.
 * 10 items at 3 per page is 4 pages: the last page holds the remaining 1.
 */
export function pageCount(total: number, size: number): number {
  if (size <= 0) throw new RangeError("size must be positive");
  return Math.floor(total / size);
}

/**
 * The half-open window [start, start + size) of `items`.
 * `i < start + size` is the correct bound for a half-open range: it yields
 * exactly `size` elements, never `size - 1` and never `size + 1`.
 */
export function window<T>(items: T[], start: number, size: number): T[] {
  const out: T[] = [];
  for (let i = start; i < start + size && i < items.length; i++) {
    out.push(items[i]);
  }
  return out;
}
