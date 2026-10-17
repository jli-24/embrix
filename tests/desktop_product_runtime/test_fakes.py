from datetime import UTC, datetime

from embedded_copilot.desktop_product_runtime import (
    DesktopProductEvent,
    FakeDesktopProductEventStore,
    ProductEventType,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def event(sequence: int) -> DesktopProductEvent:
    return DesktopProductEvent(
        event_id=f"desktop-product-session1-{sequence}",
        sequence=sequence,
        product_session_id="session1",
        event_type=ProductEventType.WORKSPACE_REFRESHED,
        reference_fingerprint="sha256:" + "a" * 64,
        status=None,
        timestamp=NOW,
    )


def test_event_store_replays_and_marks_evicted_cursor_unavailable() -> None:
    store = FakeDesktopProductEventStore(capacity=2)
    store.append(event(1))
    store.append(event(2))
    store.append(event(3))
    assert store.read_after("session1", "desktop-product-session1-1") is None
    assert [
        item.sequence
        for item in store.read_after("session1", "desktop-product-session1-2") or ()
    ] == [3]
