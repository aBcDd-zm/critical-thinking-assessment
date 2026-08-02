import { describe, expect, it } from "vitest";
import { shouldShowInterpretiveResults } from "./reportVisibility";

describe("shouldShowInterpretiveResults", () => {
  it("hides interpretive results for invalid measurements", () => {
    expect(shouldShowInterpretiveResults("invalid")).toBe(false);
  });

  it("keeps valid, caution, and legacy reports visible", () => {
    expect(shouldShowInterpretiveResults("valid")).toBe(true);
    expect(shouldShowInterpretiveResults("caution")).toBe(true);
    expect(shouldShowInterpretiveResults(undefined)).toBe(true);
    expect(shouldShowInterpretiveResults(null)).toBe(true);
  });
});
