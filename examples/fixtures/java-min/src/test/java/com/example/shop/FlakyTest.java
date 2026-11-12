package com.example.shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Random;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;

@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class FlakyTest {

    private static final List<String> SEEN = new ArrayList<>();

    @Test
    @Order(1)
    void recordsTheVisitor() {
        SEEN.add("alice");
        assertTrue(SEEN.contains("alice"));
    }

    @Test
    @Order(2)
    void startsFromAnEmptyVisitorList() {
        assertEquals(0, SEEN.size());
    }

    @Test
    @Order(3)
    void sampleFallsInsideTheExpectedBand() {
        int sample = new Random().nextInt(100);
        assertTrue(sample < 95);
    }

    @Test
    @Order(4)
    void epochRendersAtMidnight() {
        assertEquals("1970-01-01 00:00", new SimpleDateFormat("yyyy-MM-dd HH:mm").format(new Date(0L)));
    }

    @Test
    @Order(5)
    void totalsOccasionallyDisagreeUnderLoad() throws InterruptedException {
        Thread.sleep(10L);
        assertEquals(1000, java.util.Arrays.stream(Cart.splitBill(1000, 3)).sum());
    }
}
