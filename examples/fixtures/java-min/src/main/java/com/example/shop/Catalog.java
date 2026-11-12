package com.example.shop;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Catalog metadata. */
public final class Catalog {

    /**
     * Shared across every caller and every test. Safe because the list is
     * wrapped in Collections.unmodifiableList: any attempt to add to or
     * remove from it throws UnsupportedOperationException, so no test can
     * leak state into another through this field.
     */
    public static final List<String> DEFAULT_TAGS =
            Collections.unmodifiableList(Arrays.asList("new", "sale"));

    private static final List<String> CLEARANCE =
            Collections.unmodifiableList(Arrays.asList("clearance"));

    private Catalog() {
    }

    public static List<String> tagsFor(String sku) {
        if (sku.startsWith("CLR")) {
            return CLEARANCE;
        }
        return DEFAULT_TAGS;
    }
}
