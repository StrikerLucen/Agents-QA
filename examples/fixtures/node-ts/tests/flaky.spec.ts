import { describe, it, expect } from "vitest";
import { pageCount } from "../src/pagination";
import { sleep } from "../src/retry";

// Shared across the whole file on purpose.
const seen: string[] = [];

describe("flaky", () => {
  it("records the visitor it was given", () => {
    seen.push("alice");
    expect(seen).toContain("alice");
  });

  it("starts from an empty visitor list", () => {
    expect(seen.length).toBe(0);
  });

  it("samples a value inside the expected band", () => {
    const sample = Math.floor(Math.random() * 100);
    expect(sample).toBeLessThan(95);
  });

  it("renders the epoch as a local date", () => {
    const rendered = new Date(0).toLocaleDateString("en-US");
    expect(rendered).toBe("1/1/1970");
  });

  it("occasionally disagrees about page counts under load", async () => {
    await sleep(1);
    expect(pageCount(10, 3)).toBe(4);
  });
});
