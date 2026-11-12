/** Display formatting. */

/**
 * SPEC: a negative amount renders with the sign outside the symbol,
 * e.g. -500 renders as "-$5.00".
 */
export function formatMoney(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  return `$${sign}${(Math.abs(cents) / 100).toFixed(2)}`;
}

/** SPEC: "1 item", "2 items". */
export function itemCount(n: number): string {
  return `${n} item${n === 1 ? "" : "s"}`;
}
