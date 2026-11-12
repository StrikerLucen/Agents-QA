package com.example.shop;

import java.util.concurrent.Callable;

/** Retry with exponential backoff. */
public final class Backoff {

    /** Injected so no test ever waits on the real clock. */
    public interface Sleeper {
        void sleep(long millis) throws InterruptedException;
    }

    /**
     * Real wall-clock wait. This is PRODUCTION code: an exponential backoff
     * between attempts against a remote service is the documented behaviour,
     * not a race-condition workaround. Tests pass their own Sleeper.
     */
    public static final Sleeper REAL = Thread::sleep;

    private Backoff() {
    }

    public static <T> T retry(Callable<T> op, int attempts, long baseMillis, Sleeper sleeper)
            throws Exception {
        Exception last = null;
        for (int attempt = 0; attempt < attempts; attempt++) {
            try {
                return op.call();
            } catch (Exception e) {
                last = e;
                if (attempt < attempts - 1) {
                    sleeper.sleep(Math.min(baseMillis << attempt, 1000L));
                }
            }
        }
        throw last;
    }
}
