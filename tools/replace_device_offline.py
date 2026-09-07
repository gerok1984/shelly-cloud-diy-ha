#!/usr/bin/env python3
"""Swap a dead device for a new one in Home Assistant's registries, offline.

Why this exists
---------------
When a Shelly dies and you fit a replacement, Home Assistant treats the new
unit as a stranger: new device, new entities, new entity ids. Every automation,
script, dashboard card and history graph that pointed at the old entity ids is
now pointing at nothing. The integration's own
``shelly_cloud_diy.replace_device`` service solves that for devices *this*
integration owns. It cannot solve it for devices owned by somebody else's
integration — most importantly the built-in, local Shelly integration, which is
where most people's Shellys actually live.

This tool does. It is deliberately **not** part of the integration:

* It edits registries that belong to another integration. Doing that live, from
  inside a running Home Assistant, is exactly the failure mode Home Assistant's
  own maintainers warned about — silent, plausible, wrong writes that nobody
  notices until an automation stops firing.
* So it runs with Home Assistant **stopped**, prints what it would do, and only
  writes when you say so. The operation becomes something a human reviewed,
  rather than something a service call did.
* It backs up every file it touches before writing, and re-reads what it wrote.

What it rewrites
----------------
1. ``core.config_entries`` — the old entry's ``unique_id`` and, where the
   integration stores one, its host/address. Integrations that create one entry
   per device (the built-in Shelly integration does) also get the new device's
   duplicate entry removed.
2. ``core.device_registry`` — the **old** device entry is re-pointed at the new
   hardware: its ``identifiers`` and MAC ``connections`` are rewritten and the
   duplicate device entry for the new hardware is removed. The old entry's
   ``id`` survives, which is what keeps device-based automations, the area and
   any labels attached.
3. ``core.entity_registry`` — every entity of that device gets the old MAC in
   its ``unique_id`` replaced by the new one. ``entity_id`` is never touched:
   that is the whole point.

What it does not do
-------------------
It does not talk to any device, does not know whether the new hardware is the
same model, and does not migrate history — the recorder keys on ``entity_id``,
which is preserved, so history follows by itself.

Usage
-----
    # look first — this is the default, it writes nothing
    python3 tools/replace_device_offline.py --config /path/to/ha/config \\
        --old AABBCCDDEEFF --new 112233445566

    # then, with Home Assistant stopped:
    python3 tools/replace_device_offline.py --config /path/to/ha/config \\
        --old AABBCCDDEEFF --new 112233445566 --ha-is-stopped --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

CONFIG_ENTRIES = "core.config_entries"
DEVICE_REGISTRY = "core.device_registry"
ENTITY_REGISTRY = "core.entity_registry"

_MAC_12 = re.compile(r"^[0-9a-fA-F]{12}$")


class PlanError(Exception):
    """The requested swap cannot be described, so it must not be attempted."""


# ── MAC helpers ──────────────────────────────────────────────────────────────


def normalise_mac(value: str) -> str:
    """Return a bare 12-digit lowercase MAC, or raise.

    Accepts the three spellings that turn up in Home Assistant storage and in
    Shelly's own labels: ``AA:BB:CC:DD:EE:FF``, ``aa-bb-cc-dd-ee-ff`` and the
    bare form. Anything else is rejected rather than guessed at — a mistyped
    MAC that still parses would rewrite the wrong device's entities.
    """
    bare = value.replace(":", "").replace("-", "").strip()
    if not _MAC_12.match(bare):
        raise PlanError(f"{value!r} is not a MAC address")
    return bare.lower()


def _match_case(sample: str, replacement: str) -> str:
    """Return ``replacement`` in the case pattern of ``sample``."""
    if sample.isupper():
        return replacement.upper()
    if sample.islower():
        return replacement.lower()
    return replacement


def substitute_mac(text: str, old: str, new: str) -> str:
    """Replace every spelling of ``old`` in ``text`` with ``new``.

    Home Assistant is not consistent about how a MAC is written inside a
    ``unique_id``: the built-in Shelly integration uses upper case without
    separators (``AABBCCDDEEFF-switch-0``), this project uses lower case
    (``aabbccddeeff_switch_0``), and ``connections`` uses colons. All three are
    handled, and each match keeps the case it had, so the rewritten id looks
    like the integration's own work rather than like a patch.
    """
    if not text:
        return text
    colon_old = ":".join(old[i : i + 2] for i in range(0, 12, 2))
    colon_new = ":".join(new[i : i + 2] for i in range(0, 12, 2))
    for o, n in ((colon_old, colon_new), (old, new)):
        pattern = re.compile(re.escape(o), re.IGNORECASE)
        text = pattern.sub(lambda m, n=n: _match_case(m.group(0), n), text)
    return text


def contains_mac(text: Any, mac: str) -> bool:
    """Whether ``text`` mentions ``mac`` in any of its spellings."""
    if not isinstance(text, str):
        return False
    colon = ":".join(mac[i : i + 2] for i in range(0, 12, 2))
    low = text.lower()
    return mac in low or colon in low


# ── Storage access ───────────────────────────────────────────────────────────


def load_store(storage: Path, name: str) -> dict[str, Any]:
    """Read one Home Assistant storage file."""
    path = storage / name
    if not path.is_file():
        raise PlanError(f"{path} not found — is --config the Home Assistant config directory?")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_store(storage: Path, name: str, payload: dict[str, Any], stamp: str) -> Path:
    """Back up, write, and read back one storage file.

    The read-back is not ceremony. A truncated or non-JSON registry is the one
    failure mode of this tool that a user cannot recover from without a backup,
    so the tool proves the file it just wrote still parses before moving on.
    """
    path = storage / name
    backup = storage / f"{name}.bak-{stamp}"
    shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    with tmp.open(encoding="utf-8") as handle:
        json.load(handle)
    tmp.replace(path)
    return backup


# ── Planning ─────────────────────────────────────────────────────────────────


def _devices(reg: dict[str, Any]) -> list[dict[str, Any]]:
    return reg.get("data", {}).get("devices", [])


def _entities(reg: dict[str, Any]) -> list[dict[str, Any]]:
    return reg.get("data", {}).get("entities", [])


def _device_domains(device: dict[str, Any]) -> set[str]:
    """The integrations a device registry entry belongs to."""
    return {dom for dom, _ident in device.get("identifiers") or []}


def find_device(reg: dict[str, Any], mac: str, domain: str) -> dict[str, Any] | None:
    """Find the device registry entry for one MAC within one integration.

    Two places carry the MAC and either may be the one that matches: the
    integration's own ``identifiers`` and the generic ``connections`` list.
    Both are checked, but only for devices that belong to ``domain`` — a MAC is
    not unique across integrations, and rewriting the wrong integration's
    device is the mistake this whole tool exists to avoid.
    """
    for device in _devices(reg):
        if domain not in _device_domains(device):
            continue
        for entry_domain, ident in device.get("identifiers") or []:
            if entry_domain == domain and contains_mac(ident, mac):
                return device
        for conn_type, value in device.get("connections") or []:
            if conn_type == "mac" and contains_mac(value, mac):
                return device
    return None


def build_plan(
    stores: dict[str, dict[str, Any]],
    old: str,
    new: str,
    domain: str,
    new_host: str | None = None,
) -> dict[str, Any]:
    """Describe the swap without changing anything.

    Raises:
        PlanError: whenever the swap cannot be described exactly. Every such
            case is a refusal, never a best-effort write: a half-understood
            registry is precisely where silent corruption comes from.
    """
    dev_reg = stores[DEVICE_REGISTRY]
    ent_reg = stores[ENTITY_REGISTRY]
    entries = stores[CONFIG_ENTRIES]

    old_dev = find_device(dev_reg, old, domain)
    if old_dev is None:
        raise PlanError(
            f"No device of integration {domain!r} carries the old MAC {old}. "
            "Check --domain and the MAC."
        )
    new_dev = find_device(dev_reg, new, domain)

    old_entities = [e for e in _entities(ent_reg) if e.get("device_id") == old_dev["id"]]
    rewrites = [
        (e["entity_id"], e["unique_id"], substitute_mac(e["unique_id"], old, new))
        for e in old_entities
        if contains_mac(e.get("unique_id"), old)
    ]
    rewrites = [(eid, o, n) for eid, o, n in rewrites if o != n]
    if not rewrites:
        raise PlanError(
            "The old device has no entity whose unique_id mentions its MAC — "
            "this integration does not key entities on the MAC, so a MAC swap "
            "would change nothing. Nothing was touched."
        )

    dup_entities = (
        [e["entity_id"] for e in _entities(ent_reg) if e.get("device_id") == new_dev["id"]]
        if new_dev
        else []
    )

    old_entry_ids = set(old_dev.get("config_entries") or [])
    new_entry_ids = set(new_dev.get("config_entries") or []) if new_dev else set()

    # The address the integration will connect to. A MAC swap alone leaves the
    # entry pointing at the dead unit's address, which looks like a successful
    # swap right up until the first connection attempt. Prefer what the user
    # passed; otherwise inherit it from the entry Home Assistant already made
    # for the new hardware, which is the case where we can know it.
    host = new_host
    if host is None:
        for entry in entries.get("data", {}).get("entries", []):
            if entry.get("entry_id") in new_entry_ids:
                candidate = (entry.get("data") or {}).get("host")
                if isinstance(candidate, str) and candidate:
                    host = candidate
                break

    entry_changes = []
    stores_a_host = False
    for entry in entries.get("data", {}).get("entries", []):
        if entry.get("entry_id") in old_entry_ids and entry.get("domain") == domain:
            change: dict[str, Any] = {"entry_id": entry["entry_id"], "fields": {}}
            if contains_mac(entry.get("unique_id"), old):
                change["fields"]["unique_id"] = (
                    entry["unique_id"],
                    substitute_mac(entry["unique_id"], old, new),
                )
            for key, value in (entry.get("data") or {}).items():
                if contains_mac(value, old):
                    change["fields"][f"data.{key}"] = (
                        value,
                        substitute_mac(value, old, new),
                    )
            current_host = (entry.get("data") or {}).get("host")
            stores_a_host = stores_a_host or current_host is not None
            if host is not None and current_host != host:
                change["fields"]["data.host"] = (current_host, host)
            if change["fields"]:
                entry_changes.append(change)

    return {
        "old_mac": old,
        "new_mac": new,
        "domain": domain,
        "old_device": old_dev,
        "new_device": new_dev,
        "rewrites": rewrites,
        "duplicate_entities": dup_entities,
        "entry_changes": entry_changes,
        "remove_entry_ids": sorted(new_entry_ids - old_entry_ids),
        "new_host": host,
        # Only integrations that reach the device themselves keep an address.
        # A cloud-backed one has none, and warning about an address it never
        # had would send the reader looking for a setting that does not exist.
        "address_unknown": stores_a_host and host is None,
    }


def render_plan(plan: dict[str, Any]) -> str:
    """Render the plan as the text a human reads before saying yes."""
    old_dev = plan["old_device"]
    new_dev = plan["new_device"]
    name = old_dev.get("name_by_user") or old_dev.get("name") or old_dev["id"]
    lines = [
        f"Replace {plan['old_mac']} -> {plan['new_mac']}  (integration: {plan['domain']})",
        "",
        f"  Keep device : {name}",
        f"                registry id {old_dev['id']} is preserved, so device-based",
        f"                automations, its area and its labels survive.",
    ]
    if new_dev:
        dup_name = new_dev.get("name_by_user") or new_dev.get("name") or new_dev["id"]
        lines += [
            f"  Remove      : the duplicate device Home Assistant created for the new",
            f"                hardware ({dup_name}, {len(plan['duplicate_entities'])} entities)",
        ]
    else:
        lines.append("  New device  : not yet added to Home Assistant — nothing to merge")
    for change in plan["entry_changes"]:
        lines.append(f"  Config entry {change['entry_id']}:")
        for field, (before, after) in change["fields"].items():
            lines.append(f"      {field}: {before} -> {after}")
    for entry_id in plan["remove_entry_ids"]:
        lines.append(f"  Remove config entry {entry_id} (the new hardware's own entry)")
    if plan.get("address_unknown"):
        lines += [
            "  NOTE        : no address for the new hardware was given or found, so the",
            "                entry keeps the old one. If the replacement has a different",
            "                IP, pass --new-host, or let discovery correct it afterwards.",
        ]
    lines.append(f"  Rewrite {len(plan['rewrites'])} entity unique_id(s); entity_id unchanged:")
    for entity_id, before, after in plan["rewrites"]:
        lines.append(f"      {entity_id}")
        lines.append(f"          {before} -> {after}")
    return "\n".join(lines)


# ── Applying ─────────────────────────────────────────────────────────────────


def apply_plan(stores: dict[str, dict[str, Any]], plan: dict[str, Any]) -> None:
    """Apply the plan to the in-memory stores, in dependency order."""
    dev_reg = stores[DEVICE_REGISTRY]
    ent_reg = stores[ENTITY_REGISTRY]
    entries = stores[CONFIG_ENTRIES]
    old, new = plan["old_mac"], plan["new_mac"]

    # 1. The duplicates first: they hold the unique_ids and identifiers the
    #    rewrite below is about to claim.
    dup = set(plan["duplicate_entities"])
    if dup:
        ent_reg["data"]["entities"] = [
            e for e in _entities(ent_reg) if e["entity_id"] not in dup
        ]
    ent_reg["data"]["deleted_entities"] = [
        e
        for e in ent_reg.get("data", {}).get("deleted_entities", [])
        if not contains_mac(e.get("unique_id"), new)
    ]
    if plan["new_device"]:
        new_id = plan["new_device"]["id"]
        dev_reg["data"]["devices"] = [
            d for d in _devices(dev_reg) if d["id"] != new_id
        ]
    remove_entries = set(plan["remove_entry_ids"])
    if remove_entries:
        entries["data"]["entries"] = [
            e
            for e in entries.get("data", {}).get("entries", [])
            if e.get("entry_id") not in remove_entries
        ]

    # 2. Re-point the surviving device entry at the new hardware.
    device = next(d for d in _devices(dev_reg) if d["id"] == plan["old_device"]["id"])
    device["identifiers"] = [
        [dom, substitute_mac(ident, old, new)] for dom, ident in device["identifiers"]
    ]
    device["connections"] = [
        [kind, substitute_mac(value, old, new) if kind == "mac" else value]
        for kind, value in device.get("connections") or []
    ]
    if isinstance(device.get("name"), str):
        device["name"] = substitute_mac(device["name"], old, new)

    # 3. Config entries.
    by_id = {c["entry_id"]: c for c in plan["entry_changes"]}
    for entry in entries.get("data", {}).get("entries", []):
        change = by_id.get(entry.get("entry_id"))
        if not change:
            continue
        for field, (_before, after) in change["fields"].items():
            if field == "unique_id":
                entry["unique_id"] = after
            elif field.startswith("data."):
                entry["data"][field[len("data.") :]] = after

    # 4. The entities themselves, last: by now nothing else claims their ids.
    wanted = {eid: after for eid, _before, after in plan["rewrites"]}
    for entity in _entities(ent_reg):
        if entity["entity_id"] in wanted:
            entity["unique_id"] = wanted[entity["entity_id"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Swap a dead device for a new one in Home Assistant's "
        "registries, with Home Assistant stopped.",
    )
    parser.add_argument("--config", required=True, type=Path,
                        help="Home Assistant configuration directory")
    parser.add_argument("--old", required=True, help="MAC of the dead device")
    parser.add_argument("--new", required=True, help="MAC of the replacement")
    parser.add_argument("--domain", default="shelly",
                        help="Integration that owns the device (default: shelly)")
    parser.add_argument("--new-host", default=None,
                        help="Address of the replacement, if the integration stores one "
                             "and Home Assistant has not already discovered it")
    parser.add_argument("--apply", action="store_true",
                        help="Write the change. Without this, nothing is written.")
    parser.add_argument("--ha-is-stopped", action="store_true",
                        help="Confirm Home Assistant is stopped. Required with --apply, "
                             "because a running instance rewrites these files from "
                             "memory and would discard the change.")
    args = parser.parse_args(argv)

    storage = args.config / ".storage"
    try:
        old = normalise_mac(args.old)
        new = normalise_mac(args.new)
        if old == new:
            raise PlanError("The old and new MAC are the same.")
        stores = {
            name: load_store(storage, name)
            for name in (CONFIG_ENTRIES, DEVICE_REGISTRY, ENTITY_REGISTRY)
        }
        plan = build_plan(stores, old, new, args.domain, args.new_host)
    except PlanError as err:
        print(f"Refused: {err}", file=sys.stderr)
        return 2

    print(render_plan(plan))

    if not args.apply:
        print("\nDry run — nothing was written. Re-run with --ha-is-stopped --apply.")
        return 0
    if not args.ha_is_stopped:
        print("\nRefused: --apply needs --ha-is-stopped. Stop Home Assistant first;\n"
              "a running instance holds these registries in memory and would\n"
              "overwrite the change on its next save.", file=sys.stderr)
        return 2

    apply_plan(stores, plan)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backups = [save_store(storage, name, payload, stamp) for name, payload in stores.items()]
    print("\nApplied. Backups written:")
    for backup in backups:
        print(f"  {backup}")
    print("Start Home Assistant and check one of the rewritten entities.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
