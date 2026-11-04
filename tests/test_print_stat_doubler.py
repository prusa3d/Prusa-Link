"""Tests of the print stat line doubling"""
from unittest.mock import MagicMock

from prusa.link.printer_adapter.print_stat_doubler import (  # type:ignore
    PrintStatDoubler,
)
from prusa.link.printer_adapter.structures.regular_expressions import (  # type:ignore  # noqa: E501
    CONFIRMATION_REGEX,
    PRINT_INFO_REGEX,
)

# pylint: disable=comparison-with-callable

SILENT = PRINT_INFO_REGEX.match(
    "SILENT MODE: Percent done: 10; print time remaining in mins: 5; "
    "Change in mins: -1")
NORMAL = PRINT_INFO_REGEX.match(
    "NORMAL MODE: Percent done: 11; print time remaining in mins: 6; "
    "Change in mins: 2")


def make():
    """The doubler and its collaborators"""
    parser, polling = MagicMock(), MagicMock()
    return PrintStatDoubler(parser, polling), parser, polling


def test_handlers_registered():
    """Both kinds of lines are listened to"""
    doubler, parser, _ = make()
    registered = {call.args[0]: call.args[1]
                  for call in parser.add_decoupled_handler.call_args_list}
    assert registered[PRINT_INFO_REGEX] == doubler.matched
    assert registered[CONFIRMATION_REGEX] == doubler.reset


def test_pair_is_passed_along():
    """The pair of lines goes to the polling together"""
    doubler, _, polling = make()
    received = []
    polling.print_info_handler.side_effect = \
        lambda sender, matches: received.append((sender, list(matches)))
    doubler.matched(object(), SILENT)
    assert not received
    doubler.matched(object(), NORMAL)
    assert received == [(doubler, [SILENT, NORMAL])]
    assert not doubler.matches


def test_confirmation_drops_a_lonely_line():
    """The line without a pair is forgotten on the next confirmation"""
    doubler, _, polling = make()
    doubler.matched(object(), SILENT)
    doubler.reset(object(), CONFIRMATION_REGEX.match("ok"))
    assert not doubler.matches
    doubler.matched(object(), NORMAL)
    polling.print_info_handler.assert_not_called()
