import queue

from kitchen_display import events
from kitchen_display.hw import buttons, presence

CFG = {"long_press_s": 2.0, "debounce_s": 0.2}


def test_short_and_long_presses_are_classified_by_duration():
    assert buttons.classify(0.1, CFG) is False
    assert buttons.classify(1.9, CFG) is False
    assert buttons.classify(2.5, CFG) is True


def test_debouncer_drops_a_second_edge_inside_the_window():
    d = buttons.Debouncer(CFG)
    assert d.accept("A", 10.00) is True
    assert d.accept("A", 10.05) is False      # bounce
    assert d.accept("A", 10.30) is True       # a real second press


def test_debouncer_tracks_each_button_separately():
    d = buttons.Debouncer(CFG)
    assert d.accept("A", 10.00) is True
    assert d.accept("B", 10.01) is True


def test_every_button_has_a_pin():
    assert set(buttons.PINS) == {"A", "B", "C", "D"}
    assert sorted(buttons.PINS.values()) == [5, 6, 16, 24]


def test_keyboard_buttons_translate_keys_into_events():
    q = queue.Queue()
    kb = buttons.KeyboardButtons(q, CFG)
    kb.handle_key("b")
    kb.handle_key("A")
    first, second = q.get_nowait(), q.get_nowait()
    assert first == events.ButtonPressed("B", long=False)
    assert second == events.ButtonPressed("A", long=True)


def test_keyboard_buttons_ignore_unmapped_keys():
    q = queue.Queue()
    buttons.KeyboardButtons(q, CFG).handle_key("z")
    assert q.empty()


def test_always_empty_presence_reports_an_empty_room():
    assert presence.AlwaysEmpty().occupied() is False


def test_gpio_edges_become_short_and_long_presses_on_release():
    # Active-low: falling edge = pressed, rising edge = released. Classification
    # happens on release, from the time held — this is what D-long restart rides on.
    q = queue.Queue()
    g = buttons.GpioButtons(q, CFG)
    g._edge("D", pressed=True, at_s=10.0)
    assert q.empty()                           # nothing until release
    g._edge("D", pressed=False, at_s=10.3)
    g._edge("D", pressed=True, at_s=20.0)
    g._edge("D", pressed=False, at_s=22.5)
    assert q.get_nowait() == events.ButtonPressed("D", long=False)
    assert q.get_nowait() == events.ButtonPressed("D", long=True)


def test_gpio_release_bounce_does_not_double_fire():
    q = queue.Queue()
    g = buttons.GpioButtons(q, CFG)
    g._edge("A", pressed=True, at_s=10.0)
    g._edge("A", pressed=False, at_s=10.2)
    g._edge("A", pressed=True, at_s=10.21)     # contact bounce
    g._edge("A", pressed=False, at_s=10.22)
    assert q.qsize() == 1
