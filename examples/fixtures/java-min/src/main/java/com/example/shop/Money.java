package com.example.shop;

/** Numeric helpers for money-adjacent fractions. */
public final class Money {

    private Money() {
    }

    /**
     * True for exactly one quarter. 0.25 is a dyadic rational, so it has an
     * exact IEEE-754 double representation and {@code ==} against it is
     * deterministic for every exactly-representable input.
     */
    public static boolean isQuarter(double value) {
        return value == 0.25;
    }

    /** Halve a value. Exact for any exactly-representable input. */
    public static double half(double value) {
        return value / 2.0;
    }
}
