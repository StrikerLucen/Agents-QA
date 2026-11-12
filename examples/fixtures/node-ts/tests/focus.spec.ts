import { describe, it, expect } from "vitest";
import { itemCount } from "../src/format";

describe("labels", () => {
  it.only("labels two items", () => {
    expect(itemCount(2)).toBe("2 items");
  });

  it("labels ten items", () => {
    expect(itemCount(10)).toBe("10 items");
  });

  it("labels zero items", () => {
    expect(itemCount(0)).toBe("0 items");
  });
});
