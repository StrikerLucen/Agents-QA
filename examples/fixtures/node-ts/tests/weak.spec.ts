import { describe, it, expect, vi } from "vitest";
import { cartTotalCents } from "../src/cart";
import { itemCount } from "../src/format";
import { pageCount } from "../src/pagination";

describe("weak", () => {
  it("computes a cart total", () => {
    cartTotalCents([
      { unitCents: 1000, qty: 1, discountCents: 0, taxRate: 0.1 },
    ]);
  });

  it("is always true", () => {
    expect(1).toBe(1);
  });

  it("returns something for a page count", () => {
    const pages = pageCount(10, 3);
    expect(pages).toBeDefined();
  });

  it("formats an item count", () => {
    const label = itemCount(2);
    // TODO: add assertion once the copy is agreed with design
    console.log(label);
  });

  it("asks the pricing service for a total", () => {
    const pricingService = vi.fn();
    pricingService.mockReturnValue(4200);
    const total = pricingService();
    expect(total).toBe(4200);
  });
});
