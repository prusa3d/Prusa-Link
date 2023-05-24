"""Tests of the LCD Printer component"""
from time import time

from prusa.link.printer_adapter.structures.carousel import (  # type:ignore
    Carousel,
    LCDLine,
    Screen,
)


def test_line():
    """Test that the LCDLine object works as expected"""
    line = LCDLine("Derpy is best pony",
                   delay=5,
                   resets_idle=True,
                   chime_gcode=["M300 S900 P1"])
    assert line.text == "Derpy is best pony"
    assert line.delay == 5
    assert line.resets_idle
    assert line.ends_at > time() + 4.5
    assert line.chime_gcode == ["M300 S900 P1"]
    line.ends_at = time() + 1
    line.reset_end()
    assert line.ends_at > time() + 4.5


def test_screen_lines():
    """Tests that a screen with set text outputs the correct lines
    with the right properties attached"""
    screen = Screen(resets_idle=False)
    carousel = Carousel(screens=[screen])
    carousel.set_text(
        screen=screen,
        text="I tried searching for an interesting text and got sidetracked.",
        scroll_delay=1,
        first_line_extra=3,
        last_line_extra=5,
        scroll_amount=3)
    lines = list(screen.lines())
    first = lines[0]
    second = lines[1]
    second_to_last = lines[-2]
    last = lines[-1]
    assert first.text == "I tried searching f"
    assert first.delay == 4
    assert second.text == "ried searching for "
    assert second.delay == 1
    assert second_to_last.text == "and got sidetracked"
    assert second_to_last.delay == 1
    assert last.text == "nd got sidetracked."
    assert last.delay == 6
    assert len(lines) == 16

    carousel.set_text(screen=screen,
                      text="A",
                      scroll_delay=1,
                      first_line_extra=0,
                      last_line_extra=0)
    line = list(screen.lines())[0]
    assert line.text == "A"
    assert line.delay == 1


def test_priority():
    """Tests that the bigger priority gets shown and smaller gets hidden"""
    screen_a = Screen(order=1)
    screen_b = Screen(order=2)
    screen_c = Screen(order=3)

    carousel = Carousel(screens=[screen_a, screen_b, screen_c])
    carousel.set_text(screen_a, "A")
    carousel.set_text(screen_b, "B")
    carousel.set_text(screen_c, "C")
    carousel.set_priority(screen_a, 1)
    carousel.set_priority(screen_b, 2)
    carousel.set_priority(screen_c, 2)

    carousel.enable(screen_b)
    assert carousel.active_set == {screen_b}
    carousel.enable(screen_a)
    assert carousel.active_set == {screen_b}
    carousel.enable(screen_c)
    assert carousel.active_set == {screen_b, screen_c}
    carousel.set_priority(screen_a, 3)
    assert carousel.active_set == {screen_a}
    carousel.disable(screen_a)
    assert carousel.active_set == {screen_b, screen_c}
    assert carousel.active_screens == [screen_b, screen_c]
    carousel.set_priority(screen_a, 2)
    carousel.enable(screen_a)
    assert carousel.active_set == {screen_a, screen_b, screen_c}
    assert carousel.active_screens == [screen_a, screen_b, screen_c]


def test_lines():
    """Tests the get_next output"""
    screen_a = Screen(order=1, resets_idle=False)
    screen_b = Screen(order=2, chime_gcode=["M300 S900 P1"], resets_idle=True)
    screen_c = Screen(order=3)
    carousel = Carousel(screens=[screen_a, screen_b, screen_c])
    carousel.set_text(screen_a, "A")
    carousel.set_text(screen_b, "B")
    carousel.set_text(screen_c, "C")
    carousel.set_priority(screen_a, 2)
    carousel.set_priority(screen_b, 2)
    carousel.set_priority(screen_c, 1)
    carousel.enable(screen_a)
    carousel.enable(screen_b)
    carousel.enable(screen_c)

    # Test normal operation, shows lines in order
    a_line = carousel.get_next()
    assert a_line.text == "A"
    assert not a_line.resets_idle
    b_line = carousel.get_next()
    assert b_line.text == "B"
    assert b_line.chime_gcode == ["M300 S900 P1"]  # and chimes
    assert b_line.resets_idle
    # This time around it shouldn't chime
    assert carousel.get_next().text == "A"
    # Second time around, it should not chime
    assert carousel.get_next().chime_gcode == []
    # messages have priority
    carousel.add_message(LCDLine("asdf"))
    assert carousel.get_next().text == "asdf"
    # Setting a hidden Screen does not rewind
    assert carousel.get_next().text == "A"
    carousel.set_text(screen_c, "Not C")
    assert carousel.get_next().text == "B"
    assert carousel.get_next().text == "A"
    # setting a shown screen rewinds
    carousel.set_text(screen_b, "Very much B")
    assert carousel.get_next().text == "A"
    assert carousel.get_next().text == "Very much B"
    carousel.disable(screen_a)
    # Enabling again does not reset the chime
    carousel.enable(screen_b)
    assert carousel.get_next().chime_gcode == []
    carousel.disable(screen_b)
    carousel.enable(screen_b)
    assert carousel.get_next().chime_gcode == ["M300 S900 P1"]
    carousel.disable(screen_b)
    carousel.enable(screen_c)
    assert carousel.get_next().text == "Not C"
    carousel.disable(screen_a)
    carousel.disable(screen_c)
    assert carousel.get_next() is None
