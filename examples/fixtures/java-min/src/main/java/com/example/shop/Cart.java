package com.example.shop;

/**
 * Cart money handling.
 *
 * <p>SPEC:
 * <ul>
 *   <li>{@code splitBill} must return parts that sum back to the total; the
 *       remainder cents go to the earliest payers, one each.</li>
 *   <li>{@code format} renders as "USD 12.00" -- currency code first.</li>
 * </ul>
 */
public final class Cart {

    private Cart() {
    }

    public static int[] splitBill(int totalCents, int ways) {
        if (ways <= 0) {
            throw new IllegalArgumentException("ways must be positive");
        }
        int each = totalCents / ways;
        int[] parts = new int[ways];
        for (int i = 0; i < ways; i++) {
            parts[i] = each;
        }
        return parts;
    }

    public static String format(int cents, String currency) {
        return String.format("%.2f %s", cents / 100.0, currency);
    }
}
