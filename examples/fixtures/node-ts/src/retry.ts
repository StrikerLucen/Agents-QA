/** Retry with exponential backoff. */

export type Sleeper = (ms: number) => Promise<void>;

/**
 * Real wall-clock wait. This is PRODUCTION code, not a test: an exponential
 * backoff between attempts against a remote service is the documented
 * behaviour, not a race-condition workaround. Tests pass their own `sleeper`
 * so no suite ever waits on the clock.
 */
export const sleep: Sleeper = (ms) =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Call `op` up to `attempts` times, waiting base * 2^n ms between attempts,
 * capped at 1000 ms. Rethrows the last error if every attempt fails.
 */
export async function withBackoff<T>(
  op: () => Promise<T>,
  attempts = 3,
  baseMs = 50,
  sleeper: Sleeper = sleep,
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      return await op();
    } catch (error) {
      lastError = error;
      if (attempt < attempts - 1) {
        await sleeper(Math.min(baseMs * 2 ** attempt, 1000));
      }
    }
  }
  throw lastError;
}
