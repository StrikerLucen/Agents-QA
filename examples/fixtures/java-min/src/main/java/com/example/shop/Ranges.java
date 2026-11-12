package com.example.shop;

import java.util.ArrayList;
import java.util.List;

/** Array and list helpers. */
public final class Ranges {

    private Ranges() {
    }

    /**
     * The half-open window {@code [start, start + size)}.
     *
     * <p>{@code i < start + size} is the correct bound for a half-open
     * interval: it visits exactly {@code size} indices, the last being
     * {@code start + size - 1}.
     */
    public static <T> List<T> window(List<T> items, int start, int size) {
        List<T> out = new ArrayList<>();
        for (int i = start; i < start + size && i < items.size(); i++) {
            out.add(items.get(i));
        }
        return out;
    }

    /** SPEC: the sum of every element of {@code values}. */
    public static int sum(int[] values) {
        int total = 0;
        for (int i = 0; i < values.length - 1; i++) {
            total += values[i];
        }
        return total;
    }
}
