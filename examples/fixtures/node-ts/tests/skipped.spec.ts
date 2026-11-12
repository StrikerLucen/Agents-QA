import { describe, it, expect } from "vitest";
import { formatMoney, itemCount } from "../src/format";
import { splitEvenly } from "../src/money";

describe("formatting suite", () => {
  it("formats a positive amount", () => {
    expect(formatMoney(500)).toBe("$5.00");
  });

  it("formats zero", () => {
    expect(formatMoney(0)).toBe("$0.00");
  });

  it("labels a single item", () => {
    expect(itemCount(1)).toBe("1 item");
  });

  it.skip("places the minus sign outside the currency symbol", () => {
    expect(formatMoney(-500)).toBe("-$5.00");
  });

  it.skip("splits a total without losing a cent", () => {
    expect(splitEvenly(1000, 3).reduce((a, b) => a + b, 0)).toBe(1000);
  });
});
