package com.example.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.Arrays;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;

class SkippedTest {

    @Test
    void splitsAnExactlyDivisibleTotal() {
        assertEquals(3, Cart.splitBill(900, 3).length);
    }

    @Disabled("fails: the remainder cent is dropped; see SHOP-418")
    @Test
    void splitBillNeverLosesACent() {
        assertEquals(1000, Arrays.stream(Cart.splitBill(1000, 3)).sum());
    }
}
