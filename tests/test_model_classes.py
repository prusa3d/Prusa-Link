"""Tests for the telemetry models"""
from prusa.link.printer_adapter.structures.model_classes import (  # type:ignore  # noqa: E501
    IndividualSlot,
    NetworkInfo,
    PPData,
    Slot,
    Telemetry,
)


def test_slot_dict_flattens_slots():
    """Slots are moved to the top level, as the Connect API wants"""
    slot = Slot(active=1, slots={"1": IndividualSlot(temp=215.0),
                                 "2": IndividualSlot(material="PLA")})
    data = slot.dict()
    assert "slots" not in data
    assert data["active"] == 1
    assert data["1"]["temp"] == 215.0
    assert data["2"]["material"] == "PLA"


def test_slot_dict_no_slots():
    """Without slots, nothing is flattened"""
    data = Slot(active=3).dict()
    assert data["active"] == 3
    assert data["slots"] is None


def test_telemetry_defaults():
    """Everything is optional"""
    telemetry = Telemetry()
    assert telemetry.temp_nozzle is None
    assert telemetry.slot is None
    assert telemetry.dict()["slot"] is None


def test_telemetry_dict_slot():
    """The slot is serialized using its own dict method"""
    telemetry = Telemetry(
        temp_nozzle=215.5,
        slot=Slot(active=1, slots={"1": IndividualSlot(temp=1.0)}))
    data = telemetry.dict()
    assert data["temp_nozzle"] == 215.5
    assert "slots" not in data["slot"]
    assert data["slot"]["1"]["temp"] == 1.0


def test_telemetry_dict_exclude_none():
    """Keyword arguments are passed down"""
    data = Telemetry(temp_bed=60.0, slot=Slot(active=2)).dict(
        exclude_none=True)
    assert data == {"temp_bed": 60.0, "slot": {"active": 2}}


def test_network_info():
    """Network info is just a data holder"""
    assert NetworkInfo().hostname is None
    assert NetworkInfo(hostname="link").hostname == "link"


def test_pp_data_defaults():
    """The RIP port is not used by default"""
    data = PPData(file_path="/a", connect_path="/b", message_number=1,
                  gcode_number=2)
    assert not data.using_rip_port
