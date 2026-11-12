/** Configuration loading. */

/**
 * Parse configuration JSON.
 *
 * Returns `null` on any parse failure instead of throwing. Nothing is
 * swallowed: `loadConfig` below is the only caller, and it turns `null` into
 * a thrown error. The sentinel exists so callers that want to probe a
 * candidate file can do so without exception handling.
 */
export function parseConfig(raw: string): Record<string, unknown> | null {
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** The only caller of `parseConfig`. Raises on anything it cannot parse. */
export function loadConfig(raw: string): Record<string, unknown> {
  const parsed = parseConfig(raw);
  if (parsed === null) {
    throw new Error("invalid configuration: not JSON");
  }
  return parsed;
}
