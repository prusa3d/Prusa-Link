"""Tests of the SD card file listing parser and of the card state"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from prusa.connect.printer.const import State

from prusa.link.const import SD_STORAGE_NAME  # type:ignore
from prusa.link.printer_adapter.filesystem import (
    sd_card as module,  # type:ignore  # noqa: E501
)
from prusa.link.printer_adapter.filesystem.sd_card import (  # type:ignore
    FileTreeParser,
    SDCard,
    alternative_filename,
    get_root,
)
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    SDState,
)
from prusa.link.printer_adapter.structures.regular_expressions import (  # type:ignore  # noqa: E501
    LFN_CAPTURE,
    SD_EJECTED_REGEX,
    SD_PRESENT_REGEX,
)

# pylint: disable=redefined-outer-name,protected-access


def fat_time(moment):
    """The FAT date and time of the datetime as the printer prints them"""
    return "0x" + format(((moment.year - 1980) << 25) | (moment.month << 21)
                         | (moment.day << 16) | (moment.hour << 11)
                         | (moment.minute << 5) | (moment.second // 2), "x")


# FAT date 2024-01-02 03:04:06
M_TIME = fat_time(datetime(2024, 1, 2, 3, 4, 6))


def parse(*lines):
    """Parses the lines of a file listing"""
    matches = [LFN_CAPTURE.match(line) for line in
               ("Begin file list", *lines, "End file list")]
    assert all(matches)
    return FileTreeParser(matches)


def names(node):
    """Names of the children"""
    return sorted(node.children)


# --- helpers ---

def test_alternative_filename():
    """The short name makes the long one unique"""
    assert alternative_filename("long", "SHORT~1.GCO", "gcode") == \
        "SHORT~1.GCO - (long).gcode"
    assert alternative_filename("long", "SDIR~1") == "SDIR~1 - (long)"


def test_root():
    """The root is a read only directory"""
    root = get_root()
    assert root.name == SD_STORAGE_NAME
    assert root.is_dir


# --- the parser ---

def test_empty_output():
    """Nothing was captured"""
    parser = FileTreeParser([])
    assert not parser.tree.children
    assert not parser.lfn_to_sfn_paths


def test_unexpected_output():
    """Without the begin and end markers nothing is parsed"""
    matches = [LFN_CAPTURE.match('A~1.GCO 1 "a.gcode"')]
    assert not FileTreeParser(matches).tree.children


def test_files_in_root():
    """Files are added with their properties and translations"""
    parser = parse(f'/A~1.GCO {M_TIME} 1234 "My file.gcode"',
                   'B~1.G 5 "script.g"')
    assert names(parser.tree) == ["My file.gcode", "script.g"]
    node = parser.tree.children["My file.gcode"]
    assert node.attrs["size"] == 1234
    assert node.attrs["m_timestamp"] == 1704164646
    assert node.attrs["sfn"] == "a~1.gco"
    assert parser.lfn_to_sfn_paths["/My file.gcode"] == "/a~1.gco"
    assert parser.sfn_to_lfn_paths["/a~1.gco"] == "/My file.gcode"
    assert parser.mixed_to_lfn_paths["/my file.gcode"] == \
        "/My file.gcode"


@pytest.mark.parametrize(("fat", "expected"), [
    (fat_time(datetime(1980, 1, 1)), 315532800),
    (fat_time(datetime(2023, 12, 31, 23, 59, 58)), 1704067198),
    # odd seconds round down
    (fat_time(datetime(2024, 2, 29, 12, 30, 1)), 1709209800),
])
def test_modification_time(fat, expected):
    """The FAT date and time is read as UTC, in two second steps"""
    parser = parse(f'A~1.GCO {fat} 10 "a.gcode"')
    assert parser.tree.children["a.gcode"].attrs["m_timestamp"] == expected


def test_file_without_long_name_is_skipped():
    """Without the long name there is nothing to show"""
    parser = parse("A~1.GCO 1234")
    assert not parser.tree.children


def test_directories():
    """Directories nest and are left again"""
    parser = parse('DIR_ENTER: /DIR~1/ "Long dir"',
                   '/DIR~1/A~1.GCO 10 "a.gcode"',
                   'DIR_ENTER: /DIR~1/SUB~1/ "Sub"',
                   '/DIR~1/SUB~1/B~1.GCO 20 "b.gcode"',
                   "DIR_EXIT",
                   '/DIR~1/C~1.GCO 30 "c.gcode"',
                   "DIR_EXIT",
                   'D~1.GCO 40 "d.gcode"')
    directory = parser.tree.children["Long dir"]
    assert directory.is_dir
    assert directory.attrs["sfn"] == "DIR~1"
    assert names(directory) == ["Sub", "a.gcode", "c.gcode"]
    assert names(directory.children["Sub"]) == ["b.gcode"]
    assert "d.gcode" in parser.tree.children
    assert parser.lfn_to_sfn_paths["/Long dir/Sub/b.gcode"] == \
        "/dir~1/sub~1/b~1.gco"


def test_too_long_names_are_made_unique():
    """Long names are replaced by the short name with the long one"""
    long_name = "x" * 60
    parser = parse(f'A~1.GCO 10 "{long_name}"',
                   f'DIR_ENTER: /DIR~1/ "{long_name}"', "DIR_EXIT")
    assert "a~1.gco - (" + long_name + ").gcode" in parser.tree.children
    assert f"DIR~1 - ({long_name})" in parser.tree.children
    file_node = parser.tree.children["a~1.gco - (" + long_name + ").gcode"]
    assert file_node.attrs["filename_too_long"] is True


def test_extra_directory_exit_stays_in_the_root():
    """Leaving the root again does not leave the tree"""
    parser = parse("DIR_EXIT", 'A~1.GCO 1 "a.gcode"')
    assert names(parser.tree) == ["a.gcode"]


def test_uniqueness_conflict_is_logged(caplog):
    """Two files with the same long name"""
    with caplog.at_level("ERROR"):
        parser = parse('A~1.GCO 1 "same.gcode"', 'B~1.GCO 1 "same.gcode"')
    assert names(parser.tree) == ["same.gcode"]
    assert "name conflict" in caplog.text
    assert parser.tree.children["same.gcode"].attrs["sfn"] == "b~1.gco"


# --- the card ---

@pytest.fixture
def card():
    """The SD card with a mocked queue and no thread"""
    model = MagicMock()
    model.state_manager.current_state = State.IDLE
    with patch.object(module, "enqueue_matchable") as matchable, \
            patch.object(module, "enqueue_list_from_str") as enqueue_list, \
            patch.object(module, "wait_for_instruction"):
        card_ = SDCard(MagicMock(), MagicMock(), model)
        card_.matchable = matchable
        card_.enqueue_list = enqueue_list
        yield card_


def listing(*lines):
    """What the instruction captured"""
    instruction = MagicMock()
    instruction.get_matches.return_value = [
        LFN_CAPTURE.match(line) for line in
        ("Begin file list", *lines, "End file list")]
    return instruction


def match_of(regex, line):
    """Match of the line"""
    return regex.match(line)


def collect(signal):
    """Collects the keyword arguments of a signal"""
    received = []
    signal.connect(lambda sender, **kwargs: received.append(kwargs),
                   weak=False)
    return received


def test_initial_state(card):
    """Nothing is known"""
    assert card.data.sd_state == SDState.UNSURE
    assert card.data.invalidated is True
    registered = [call.args[0] for call in
                  card.serial_parser.add_decoupled_handler.call_args_list]
    assert registered == [SD_PRESENT_REGEX, SD_EJECTED_REGEX]


def test_update_finds_files(card):
    """Found files mean the card is present"""
    attached = collect(card.sd_attached_signal)
    updated = collect(card.tree_updated_signal)
    card.matchable.return_value = listing('A~1.GCO 10 "a.gcode"')
    card.update()
    assert card.data.sd_state == SDState.PRESENT
    assert card.data.lfn_to_sfn_paths == {"/a.gcode": "/a~1.gco"}
    assert card.data.invalidated is False
    assert attached[0]["files"] is card.data.files
    assert len(updated) == 1


@pytest.mark.parametrize("state", [State.PRINTING, State.BUSY])
def test_update_only_when_idle(card, state):
    """The card is not scanned during the print"""
    card.model.state_manager.current_state = state
    card.update()
    card.matchable.assert_not_called()


def test_update_not_invalidated(card):
    """No change, no scan"""
    card.data.invalidated = False
    card.update()
    card.matchable.assert_not_called()


def test_update_absent(card):
    """No card, no scan"""
    card.data.sd_state = SDState.ABSENT
    card.update()
    card.matchable.assert_not_called()
    assert card.data.invalidated is False


def test_update_empty_card_decides_presence(card):
    """No files and unsure state means asking the printer"""
    card.matchable.side_effect = [
        listing(),
        MagicMock(match=MagicMock(return_value=SD_PRESENT_REGEX.match(
            "echo:SD card ok"))),
    ]
    card.update()
    assert card.data.sd_state == SDState.PRESENT
    asked = card.matchable.call_args_list[1]  # after the file listing
    assert "M21" in asked.args


def test_update_after_initialising(card):
    """The card just inserted becomes present"""
    card.data.sd_state = SDState.INITIALISING
    card.matchable.return_value = listing()
    card.update()
    assert card.data.sd_state == SDState.PRESENT


def test_decide_presence_failure(card):
    """The init failure means no card"""
    card.matchable.return_value = MagicMock(match=MagicMock(
        return_value=SD_PRESENT_REGEX.match("echo:SD init fail")))
    card.decide_presence()
    assert card.data.sd_state == SDState.ABSENT
    assert card.data.expecting_insertion is False


def test_decide_presence_without_answer(card):
    """No answer changes nothing"""
    card.matchable.return_value = MagicMock(
        match=MagicMock(return_value=None))
    card.decide_presence()
    assert card.data.sd_state == SDState.UNSURE


def test_decide_presence_known_card(card):
    """A known state is not changed by a positive answer"""
    card.data.sd_state = SDState.PRESENT
    card.matchable.return_value = MagicMock(match=MagicMock(
        return_value=SD_PRESENT_REGEX.match("echo:SD card ok")))
    attached = collect(card.sd_attached_signal)
    card.decide_presence()
    assert not attached


def test_inserted_unexpectedly(card):
    """Somebody inserted a card"""
    card.data.invalidated = False
    card.sd_inserted(object(), SD_PRESENT_REGEX.match("echo:SD card ok"))
    assert card.data.sd_state == SDState.INITIALISING
    assert card.data.invalidated is True


def test_inserted_as_expected(card):
    """The answer to our own init"""
    card.data.expecting_insertion = True
    card.data.invalidated = False
    card.sd_inserted(object(), SD_PRESENT_REGEX.match("echo:SD card ok"))
    assert card.data.expecting_insertion is False
    assert card.data.invalidated is False


def test_insertion_failure_is_ignored(card):
    """The failure message is not an insertion"""
    card.sd_inserted(object(), SD_PRESENT_REGEX.match("echo:SD init fail"))
    assert card.data.sd_state == SDState.UNSURE


def test_ejected(card):
    """The card is gone, the files are forgotten"""
    card.matchable.return_value = listing('A~1.GCO 10 "a.gcode"')
    card.update()
    detached = collect(card.sd_detached_signal)
    changed = collect(card.state_changed_signal)
    card.sd_ejected(object(), SD_EJECTED_REGEX.match("echo:SD card released"))
    assert card.data.sd_state == SDState.ABSENT
    assert card.data.invalidated is True
    assert len(detached) == 1
    assert changed[-1] == {"sd_state": SDState.ABSENT}
    assert not card.data.files.children
    assert not card.data.lfn_to_sfn_paths


def test_reinserted_while_present(card):
    """New card replaces the old one"""
    card.matchable.return_value = listing('A~1.GCO 10 "a.gcode"')
    card.update()
    detached = collect(card.sd_detached_signal)
    card.sd_inserted(object(), SD_PRESENT_REGEX.match("echo:SD card ok"))
    assert len(detached) == 1
    assert card.data.sd_state == SDState.INITIALISING


def test_flash_air(card):
    """The flag is stored"""
    card.set_flash_air(True)
    assert card.data.is_flash_air is True


# --- special menu ---

MENU = ['DIR_ENTER: /MENU~1/ "PrusaLink menu"', "DIR_EXIT"]


def test_menu_is_hidden_and_prepared(card):
    """The menu folder gets the setready file and is not listed"""
    found = collect(card.menu_found_signal)
    card.matchable.return_value = listing(*MENU, 'A~1.GCO 10 "a.gcode"')
    card.update()
    assert found == [{"menu_sfn": "menu~1"}]
    assert "PrusaLink menu" not in card.data.files.children
    commands = card.enqueue_list.call_args.args[1]
    assert commands == ["M28 menu~1/setready.g", "M84", "M29"]


def test_menu_with_the_file_already(card):
    """Nothing is written when the file is there"""
    parser = parse(*MENU)
    parser.tree.children["PrusaLink menu"].children["SETREADY.G"] = object()
    card.handle_special_menu(parser)
    card.enqueue_list.assert_not_called()


def test_no_menu(card):
    """Nothing happens without the folder"""
    found = collect(card.menu_found_signal)
    card.handle_special_menu(parse('A~1.GCO 10 "a.gcode"'))
    assert not found


def test_menu_name_is_a_file(card):
    """A file named like the menu is not a menu"""
    found = collect(card.menu_found_signal)
    card.handle_special_menu(parse('M~1.GCO 10 "PrusaLink menu"'))
    assert not found
