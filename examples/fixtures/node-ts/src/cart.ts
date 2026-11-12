/**
 * Line pricing.
 *
 * SPEC (this is the contract this module is graded against):
 *   1. Tax is computed on the UNDISCOUNTED line total. The jurisdiction taxes
 *      the list price; a seller-granted discount does not reduce the tax base.
 *   2. The discount is subtracted after tax has been added.
 *   3. All money is integer cents. Rounding is half-up on the tax only.
 */

export interface Line {
  unitCents: number;
  qty: number;
  discountCents: number;
  taxRate: number;
}

export function baseCents(line: Line): number {
  return line.unitCents * line.qty;
}

export function taxCents(line: Line): number {
  return Math.round(baseCents(line) * line.taxRate);
}

export function lineTotalCents(line: Line): number {
  const base = baseCents(line);
  const discounted = base - line.discountCents;
  const tax = Math.round(discounted * line.taxRate);
  return discounted + tax;
}

export function cartTotalCents(lines: Line[]): number {
  return lines.reduce((sum, line) => sum + lineTotalCents(line), 0);
}
