package com.example.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.Test;

class GoodTest {

    @Test
    void windowReturnsExactlySizeItems() {
        List<String> items = Arrays.asList("a", "b", "c", "d", "e");
        assertEquals(Arrays.asList("b", "c", "d"), Ranges.window(items, 1, 3));
        assertEquals(items, Ranges.window(items, 0, 5));
        assertEquals(Arrays.asList("e"), Ranges.window(items, 4, 3));
    }

    @Test
    void halfIsExactForDyadicValues() {
        assertTrue(Money.isQuarter(Money.half(0.5)));
        assertFalse(Money.isQuarter(Money.half(0.25)));
        assertEquals(0.125, Money.half(0.25));
    }

    @Test
    void retryUsesTheInjectedSleeperAndReturnsTheResult() throws Exception {
        List<Long> waits = new ArrayList<>();
        int[] calls = {0};
        String result = Backoff.retry(() -> {
            calls[0]++;
            if (calls[0] < 3) {
                throw new IllegalStateException("boom");
            }
            return "done";
        }, 3, 50L, waits::add);

        assertEquals("done", result);
        assertEquals(3, calls[0]);
        assertEquals(Arrays.asList(50L, 100L), waits);
    }

    @Test
    void defaultTagsCannotBeMutatedByAnyCaller() {
        assertThrows(UnsupportedOperationException.class,
                () -> Catalog.DEFAULT_TAGS.add("hacked"));
        assertEquals(Arrays.asList("new", "sale"), Catalog.tagsFor("ABC-1"));
        assertEquals(Arrays.asList("clearance"), Catalog.tagsFor("CLR-9"));
    }
}
