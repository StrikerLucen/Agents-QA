import { describe, it, expect } from "vitest";
import { window } from "../src/pagination";
import { quarter, isHalf } from "../src/money";
import { withBackoff } from "../src/retry";
import { loadConfig } from "../src/config";

describe("window", () => {
  it("returns exactly `size` elements from a half-open range", () => {
    const items = ["a", "b", "c", "d", "e"];
    expect(window(items, 1, 3)).toEqual(["b", "c", "d"]);
    expect(window(items, 0, 5)).toEqual(items);
    expect(window(items, 4, 3)).toEqual(["e"]);
  });
});

describe("quarter", () => {
  it("divides exactly for dyadic inputs", () => {
    expect(quarter(1)).toBe(0.25);
    expect(quarter(2)).toBe(0.5);
    expect(isHalf(quarter(2))).toBe(true);
    expect(isHalf(quarter(1))).toBe(false);
  });
});

describe("withBackoff", () => {
  it("retries until the operation succeeds and reports the waits", async () => {
    const waits: number[] = [];
    const noWait = async (ms: number) => {
      waits.push(ms);
    };
    let calls = 0;
    const result = await withBackoff(
      async () => {
        calls += 1;
        if (calls < 3) throw new Error("boom");
        return "done";
      },
      3,
      50,
      noWait,
    );
    expect(result).toBe("done");
    expect(calls).toBe(3);
    expect(waits).toEqual([50, 100]);
  });

  it("rethrows the last error when every attempt fails", async () => {
    const noWait = async () => {};
    await expect(
      withBackoff(async () => {
        throw new Error("always");
      }, 2, 10, noWait),
    ).rejects.toThrow("always");
  });
});

describe("loadConfig", () => {
  it("throws on unparseable input and returns the object otherwise", () => {
    expect(() => loadConfig("{not json")).toThrow("invalid configuration");
    expect(loadConfig('{"a":1}')).toEqual({ a: 1 });
  });
});
