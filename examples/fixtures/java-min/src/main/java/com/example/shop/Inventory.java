package com.example.shop;

/** Stock lookups. */
public final class Inventory {

    /** A stocked item. {@code location} is null until the item is put away. */
    public static final class Item {
        public final String sku;
        public final Location location;

        public Item(String sku, Location location) {
            this.sku = sku;
            this.location = location;
        }
    }

    public static final class Location {
        public final String code;

        public Location(String code) {
            this.code = code;
        }
    }

    private Inventory() {
    }

    /**
     * SPEC: the bin code an item is stored in, or "UNASSIGNED" when the item
     * has not been put away yet. An item with a null location is an ordinary
     * state, not an error.
     */
    public static String locationCode(Item item) {
        if (item == null) {
            return "UNASSIGNED";
        }
        return item.location.code;
    }
}
