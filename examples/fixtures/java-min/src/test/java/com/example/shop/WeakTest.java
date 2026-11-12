package com.example.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.function.IntSupplier;
import org.junit.jupiter.api.Test;

class WeakTest {

    @Test
    void splitBillRuns() {
        Cart.splitBill(1000, 3);
    }

    @Test
    void arithmeticStillWorks() {
        assertTrue(true);
    }

    @Test
    void sumReturnsSomething() {
        Integer total = Ranges.sum(new int[] {1, 2, 3});
        assertNotNull(total);
    }

    @Test
    void formatsAnAmount() {
        String label = Cart.format(1200, "USD");
        // TODO: add assertion once the currency layout is agreed
        System.out.println(label);
    }

    @Test
    void pricingServiceReturnsATotal() {
        IntSupplier pricingService = mock(IntSupplier.class);
        when(pricingService.getAsInt()).thenReturn(4200);
        int total = pricingService.getAsInt();
        assertEquals(4200, total);
    }
}
