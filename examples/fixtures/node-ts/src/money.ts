/** Money helpers. Amounts are integer cents unless a name says otherwise. */

/**
 * Split `totalCents` across `ways` payers.
 * SPEC: the returned parts must sum back to `totalCents`; the remainder is
 * handed to the earliest payers, one cent each.
 */
export function splitEvenly(totalCents: number, ways: number): number[] {
  const each = Math.floor(totalCents / ways);
  return new Array(ways).fill(each);
}

/**
 * True for exactly one half. 0.5 is a dyadic rational, so it has an exact
 * IEEE-754 double representation and `===` against it is deterministic.
 */
export function isHalf(value: number): boolean {
  return value === 0.5;
}

/** Divide by four. Exact for any input that is itself exactly representable. */
export function quarter(value: number): number {
  return value / 4;
}
