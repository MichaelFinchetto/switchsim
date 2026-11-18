import pytest

from optiswitch.events import Clock, EventKind, EventQueue, bytes_to_gbit, ns_to_ms, ns_to_us, transmission_time_ns


def test_queue_orders_by_time_then_priority_then_seq():
    q = EventQueue()
    q.push(10, EventKind.SCHEDULE, "c")
    q.push(5, EventKind.ARRIVAL, "a")
    q.push(10, EventKind.TX_DONE, "b")
    q.push(10, EventKind.SCHEDULE, "d")
    assert [e.payload for e in q.drain()] == ["a", "b", "c", "d"]
    assert len(q) == 0 and not q


def test_queue_cancel_and_peek():
    q = EventQueue()
    e1 = q.push(1, EventKind.ARRIVAL, 1)
    e2 = q.push(2, EventKind.ARRIVAL, 2)
    q.cancel(e1)
    q.cancel(e1)  # idempotent
    assert len(q) == 1
    assert q.peek() is e2
    assert q.pop() is e2
    assert q.peek() is None
    with pytest.raises(IndexError):
        q.pop()
    with pytest.raises(ValueError):
        q.push(-1, EventKind.ARRIVAL)


def test_clock_dispatch_and_run_until():
    c = Clock()
    seen = []
    c.on(EventKind.ARRIVAL, lambda clk, ev: seen.append((clk.now_ns, ev.payload)))
    c.on(EventKind.CUSTOM, lambda clk, ev: clk.schedule(5, EventKind.ARRIVAL, "chained"))
    c.schedule_at(10, EventKind.ARRIVAL, "x")
    c.schedule_at(20, EventKind.CUSTOM)
    c.schedule_at(100, EventKind.ARRIVAL, "late")
    n = c.run(until_ns=50)
    assert n == 3
    assert seen == [(10, "x"), (25, "chained")]
    assert c.now_ns == 50
    assert c.run() == 1 and seen[-1] == (100, "late")
    assert c.processed == 4


def test_clock_errors():
    c = Clock()
    c.schedule_at(5, EventKind.ARRIVAL)
    with pytest.raises(RuntimeError):
        c.step()  # no handler
    c = Clock()
    c.now_ns = 10
    with pytest.raises(ValueError):
        c.schedule_at(5, EventKind.ARRIVAL)
    with pytest.raises(ValueError):
        c.schedule(-1, EventKind.ARRIVAL)
    assert c.step() is None


def test_clock_max_events():
    c = Clock()
    c.on(EventKind.ARRIVAL, lambda clk, ev: None)
    for t in range(5):
        c.schedule_at(t, EventKind.ARRIVAL)
    assert c.run(max_events=2) == 2
    assert len(c.queue) == 3


def test_transmission_time():
    assert transmission_time_ns(1_000_000, 100.0) == 80_000  # 8 Mbit at 100 Gbit/s
    assert transmission_time_ns(1, 100.0) == 1  # rounds up
    assert transmission_time_ns(0, 10.0) == 0
    with pytest.raises(ValueError):
        transmission_time_ns(10, 0.0)
    with pytest.raises(ValueError):
        transmission_time_ns(-1, 1.0)


def test_unit_helpers():
    assert ns_to_us(1500) == 1.5
    assert ns_to_ms(2_500_000) == 2.5
    assert bytes_to_gbit(125_000_000) == pytest.approx(1.0)
