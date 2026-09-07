"""Tests for the offline device-replacement tool.

The tool edits registries that belong to *other* integrations, with Home
Assistant stopped. That is a position of trust, and the two ways it can betray
it are the two things these tests are about:

1. **Writing the wrong thing quietly.** A MAC that appears in a second
   integration's device, or an entity whose ``entity_id`` shifts, breaks
   automations without an error anywhere. So the tests assert what must *not*
   change as carefully as what must.
2. **Writing a half-finished swap.** Every refusal path is tested, because a
   refusal is the safe outcome and a best-effort write is not.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import replace_device_offline as tool  # noqa: E402

OLD = "aabbccddeeff"
NEW = "112233445566"


# ── MAC handling ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "given",
    ["AABBCCDDEEFF", "aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aabbccddeeff"],
)
def test_every_spelling_of_a_mac_normalises_to_the_same_thing(given: str):
    assert tool.normalise_mac(given) == OLD


@pytest.mark.parametrize("given", ["", "not-a-mac", "aabbccddeef", "aabbccddeeffff", "zzbbccddeeff"])
def test_a_mac_that_does_not_parse_is_refused_rather_than_guessed(given: str):
    """A mistyped MAC that still parsed would rewrite somebody else's device."""
    with pytest.raises(tool.PlanError):
        tool.normalise_mac(given)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AABBCCDDEEFF-switch-0", "112233445566-switch-0"),   # built-in Shelly style
        ("aabbccddeeff_switch_0", "112233445566_switch_0"),   # this project's style
        ("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"),           # connections style
        ("AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"),
        ("shellyplus1pm-AABBCCDDEEFF", "shellyplus1pm-112233445566"),
        ("nothing to see here", "nothing to see here"),
    ],
)
def test_a_mac_is_replaced_in_every_spelling_and_keeps_its_case(text: str, expected: str):
    assert tool.substitute_mac(text, OLD, NEW) == expected


# ── Fixtures ────────────────────────────────────────────────────────────────


def _stores() -> dict[str, dict]:
    """A miniature Home Assistant: one dead device, its replacement, a bystander."""
    return {
        tool.CONFIG_ENTRIES: {
            "data": {
                "entries": [
                    {
                        "entry_id": "entry_old",
                        "domain": "shelly",
                        "unique_id": OLD.upper(),
                        "data": {"host": "192.168.1.10", "model": "S3SW-001P8EU"},
                    },
                    {
                        "entry_id": "entry_new",
                        "domain": "shelly",
                        "unique_id": NEW.upper(),
                        "data": {"host": "192.168.1.11"},
                    },
                    {
                        "entry_id": "entry_other",
                        "domain": "hue",
                        "unique_id": OLD.upper(),
                        "data": {},
                    },
                ]
            }
        },
        tool.DEVICE_REGISTRY: {
            "data": {
                "devices": [
                    {
                        "id": "dev_old",
                        "name": "Kitchen light",
                        "name_by_user": "Kitchen light",
                        "identifiers": [["shelly", OLD.upper()]],
                        "connections": [["mac", "aa:bb:cc:dd:ee:ff"]],
                        "config_entries": ["entry_old"],
                        "area_id": "kitchen",
                    },
                    {
                        "id": "dev_new",
                        "name": "shellyplus1pm-112233445566",
                        "identifiers": [["shelly", NEW.upper()]],
                        "connections": [["mac", "11:22:33:44:55:66"]],
                        "config_entries": ["entry_new"],
                    },
                    {
                        "id": "dev_other",
                        "name": "A Hue bulb that happens to share the MAC",
                        "identifiers": [["hue", OLD.upper()]],
                        "connections": [["mac", "aa:bb:cc:dd:ee:ff"]],
                        "config_entries": ["entry_other"],
                    },
                ]
            }
        },
        tool.ENTITY_REGISTRY: {
            "data": {
                "entities": [
                    {
                        "entity_id": "switch.kitchen_light",
                        "unique_id": f"{OLD.upper()}-switch-0",
                        "device_id": "dev_old",
                        "config_entry_id": "entry_old",
                    },
                    {
                        "entity_id": "sensor.kitchen_light_power",
                        "unique_id": f"{OLD.upper()}-switch-0-power",
                        "device_id": "dev_old",
                        "config_entry_id": "entry_old",
                    },
                    {
                        "entity_id": "switch.shellyplus1pm_112233445566_switch_0",
                        "unique_id": f"{NEW.upper()}-switch-0",
                        "device_id": "dev_new",
                        "config_entry_id": "entry_new",
                    },
                    {
                        "entity_id": "light.hue_bystander",
                        "unique_id": f"{OLD.upper()}-hue",
                        "device_id": "dev_other",
                        "config_entry_id": "entry_other",
                    },
                ],
                "deleted_entities": [
                    {"entity_id": "switch.ghost", "unique_id": f"{NEW.upper()}-switch-0"}
                ],
            }
        },
    }


def _by_entity_id(stores: dict) -> dict[str, dict]:
    return {e["entity_id"]: e for e in stores[tool.ENTITY_REGISTRY]["data"]["entities"]}


# ── Planning ────────────────────────────────────────────────────────────────


def test_the_plan_names_every_change_before_anything_is_written():
    plan = tool.build_plan(_stores(), OLD, NEW, "shelly")

    assert plan["old_device"]["id"] == "dev_old"
    assert plan["new_device"]["id"] == "dev_new"
    assert sorted(e for e, _o, _n in plan["rewrites"]) == [
        "sensor.kitchen_light_power",
        "switch.kitchen_light",
    ]
    assert plan["duplicate_entities"] == ["switch.shellyplus1pm_112233445566_switch_0"]
    assert plan["remove_entry_ids"] == ["entry_new"]

    rendered = tool.render_plan(plan)
    assert "switch.kitchen_light" in rendered
    assert f"{OLD.upper()}-switch-0 -> {NEW.upper()}-switch-0" in rendered


def test_an_unknown_old_device_is_refused():
    with pytest.raises(tool.PlanError, match="No device"):
        tool.build_plan(_stores(), "999999999999", NEW, "shelly")


def test_the_wrong_integration_is_refused_even_though_the_mac_exists():
    """The MAC is on a Hue device too. Naming the wrong domain must not match it."""
    with pytest.raises(tool.PlanError, match="No device"):
        tool.build_plan(_stores(), OLD, NEW, "esphome")


def test_an_integration_that_does_not_key_on_the_mac_is_refused():
    """Rewriting a MAC that appears nowhere in a unique_id would change nothing.

    Silently reporting success there would teach a user to trust the tool in
    exactly the case where it did not do the job.
    """
    stores = _stores()
    for entity in stores[tool.ENTITY_REGISTRY]["data"]["entities"]:
        if entity["device_id"] == "dev_old":
            entity["unique_id"] = "some-other-scheme-42"
    with pytest.raises(tool.PlanError, match="no entity whose unique_id"):
        tool.build_plan(stores, OLD, NEW, "shelly")


# ── Applying ────────────────────────────────────────────────────────────────


def test_the_swap_keeps_entity_ids_and_rewrites_only_what_it_must():
    stores = _stores()
    plan = tool.build_plan(stores, OLD, NEW, "shelly")
    tool.apply_plan(stores, plan)

    entities = _by_entity_id(stores)

    # The point of the whole exercise: the ids automations use are untouched.
    assert "switch.kitchen_light" in entities
    assert "sensor.kitchen_light_power" in entities
    assert entities["switch.kitchen_light"]["unique_id"] == f"{NEW.upper()}-switch-0"
    assert entities["sensor.kitchen_light_power"]["unique_id"] == f"{NEW.upper()}-switch-0-power"

    # The duplicate the new hardware brought with it is gone, ghosts included.
    assert "switch.shellyplus1pm_112233445566_switch_0" not in entities
    assert stores[tool.ENTITY_REGISTRY]["data"]["deleted_entities"] == []

    # The bystander on another integration is untouched, MAC and all.
    assert entities["light.hue_bystander"]["unique_id"] == f"{OLD.upper()}-hue"

    devices = {d["id"]: d for d in stores[tool.DEVICE_REGISTRY]["data"]["devices"]}
    assert "dev_new" not in devices
    assert "dev_other" in devices
    # Same registry id => device-based automations, area and labels survive.
    assert devices["dev_old"]["identifiers"] == [["shelly", NEW.upper()]]
    assert devices["dev_old"]["connections"] == [["mac", "11:22:33:44:55:66"]]
    assert devices["dev_old"]["area_id"] == "kitchen"
    assert devices["dev_old"]["name_by_user"] == "Kitchen light"

    entries = {e["entry_id"]: e for e in stores[tool.CONFIG_ENTRIES]["data"]["entries"]}
    assert "entry_new" not in entries
    assert entries["entry_old"]["unique_id"] == NEW.upper()
    # The address is inherited from the entry Home Assistant made for the new
    # hardware — a MAC swap that left the old address behind would look like a
    # success and fail at the first connection.
    assert entries["entry_old"]["data"]["host"] == "192.168.1.11"
    assert entries["entry_other"]["unique_id"] == OLD.upper()  # bystander untouched


def test_a_replacement_not_yet_added_to_home_assistant_still_works():
    """The simpler case: the user swapped the hardware and never added the new one."""
    stores = _stores()
    stores[tool.DEVICE_REGISTRY]["data"]["devices"] = [
        d for d in stores[tool.DEVICE_REGISTRY]["data"]["devices"] if d["id"] != "dev_new"
    ]
    stores[tool.ENTITY_REGISTRY]["data"]["entities"] = [
        e for e in stores[tool.ENTITY_REGISTRY]["data"]["entities"] if e["device_id"] != "dev_new"
    ]
    stores[tool.CONFIG_ENTRIES]["data"]["entries"] = [
        e for e in stores[tool.CONFIG_ENTRIES]["data"]["entries"] if e["entry_id"] != "entry_new"
    ]

    plan = tool.build_plan(stores, OLD, NEW, "shelly")
    assert plan["new_device"] is None
    assert plan["duplicate_entities"] == []
    tool.apply_plan(stores, plan)

    assert _by_entity_id(stores)["switch.kitchen_light"]["unique_id"] == f"{NEW.upper()}-switch-0"


# ── The command line ────────────────────────────────────────────────────────


def _write_config(tmp_path: Path) -> Path:
    storage = tmp_path / ".storage"
    storage.mkdir()
    for name, payload in _stores().items():
        (storage / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return tmp_path


def test_a_dry_run_writes_nothing(tmp_path: Path, capsys):
    config = _write_config(tmp_path)
    before = (config / ".storage" / tool.ENTITY_REGISTRY).read_text(encoding="utf-8")

    assert tool.main(["--config", str(config), "--old", OLD, "--new", NEW]) == 0

    assert (config / ".storage" / tool.ENTITY_REGISTRY).read_text(encoding="utf-8") == before
    assert "nothing was written" in capsys.readouterr().out


def test_apply_without_the_stopped_confirmation_is_refused(tmp_path: Path):
    """A running Home Assistant would silently discard the change on its next save."""
    config = _write_config(tmp_path)
    before = (config / ".storage" / tool.ENTITY_REGISTRY).read_text(encoding="utf-8")

    assert tool.main(["--config", str(config), "--old", OLD, "--new", NEW, "--apply"]) == 2

    assert (config / ".storage" / tool.ENTITY_REGISTRY).read_text(encoding="utf-8") == before


def test_applying_writes_backups_and_valid_json(tmp_path: Path):
    config = _write_config(tmp_path)

    code = tool.main(
        ["--config", str(config), "--old", OLD, "--new", NEW, "--apply", "--ha-is-stopped"]
    )

    assert code == 0
    storage = config / ".storage"
    for name in (tool.CONFIG_ENTRIES, tool.DEVICE_REGISTRY, tool.ENTITY_REGISTRY):
        assert json.loads((storage / name).read_text(encoding="utf-8"))
        assert list(storage.glob(f"{name}.bak-*")), f"no backup for {name}"
    entities = json.loads((storage / tool.ENTITY_REGISTRY).read_text(encoding="utf-8"))
    uids = {e["entity_id"]: e["unique_id"] for e in entities["data"]["entities"]}
    assert uids["switch.kitchen_light"] == f"{NEW.upper()}-switch-0"


def test_the_same_mac_twice_is_refused(tmp_path: Path):
    config = _write_config(tmp_path)
    assert tool.main(["--config", str(config), "--old", OLD, "--new", OLD]) == 2


def test_the_address_can_be_given_when_home_assistant_never_saw_the_new_device(tmp_path: Path):
    stores = _stores()
    for key, ident in (("devices", "dev_new"), ):
        stores[tool.DEVICE_REGISTRY]["data"][key] = [
            d for d in stores[tool.DEVICE_REGISTRY]["data"][key] if d["id"] != ident
        ]
    stores[tool.CONFIG_ENTRIES]["data"]["entries"] = [
        e for e in stores[tool.CONFIG_ENTRIES]["data"]["entries"] if e["entry_id"] != "entry_new"
    ]

    plan = tool.build_plan(stores, OLD, NEW, "shelly", new_host="10.0.0.9")
    tool.apply_plan(stores, plan)

    entries = {e["entry_id"]: e for e in stores[tool.CONFIG_ENTRIES]["data"]["entries"]}
    assert entries["entry_old"]["data"]["host"] == "10.0.0.9"


def test_an_unknown_address_is_said_out_loud_rather_than_assumed(tmp_path: Path):
    """Silence here would read as "the address is fine", which it may not be."""
    stores = _stores()
    stores[tool.CONFIG_ENTRIES]["data"]["entries"] = [
        e for e in stores[tool.CONFIG_ENTRIES]["data"]["entries"] if e["entry_id"] != "entry_new"
    ]
    stores[tool.DEVICE_REGISTRY]["data"]["devices"] = [
        d for d in stores[tool.DEVICE_REGISTRY]["data"]["devices"] if d["id"] != "dev_new"
    ]

    plan = tool.build_plan(stores, OLD, NEW, "shelly")

    assert plan["new_host"] is None
    assert plan["address_unknown"] is True
    assert "--new-host" in tool.render_plan(plan)


def test_no_address_warning_for_an_integration_that_keeps_no_address():
    """A cloud integration has no host. Warning about one sends the reader hunting."""
    stores = _stores()
    for entry in stores[tool.CONFIG_ENTRIES]["data"]["entries"]:
        entry["data"].pop("host", None)
    stores[tool.CONFIG_ENTRIES]["data"]["entries"] = [
        e for e in stores[tool.CONFIG_ENTRIES]["data"]["entries"] if e["entry_id"] != "entry_new"
    ]
    stores[tool.DEVICE_REGISTRY]["data"]["devices"] = [
        d for d in stores[tool.DEVICE_REGISTRY]["data"]["devices"] if d["id"] != "dev_new"
    ]

    plan = tool.build_plan(stores, OLD, NEW, "shelly")

    assert plan["address_unknown"] is False
    assert "--new-host" not in tool.render_plan(plan)
