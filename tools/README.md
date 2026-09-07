# Operator tools

Scripts here are **not part of the integration**. They are never packaged into
the HACS download — the release ZIP is built from `custom_components/shelly_cloud_diy`
alone — and nothing in Home Assistant runs them. You run them yourself, on
purpose, from a terminal.

## `replace_device_offline.py` — swap a dead device without losing your setup

When a device dies and you fit a replacement, Home Assistant treats the new unit
as a stranger: new device, new entities, new entity ids. Every automation,
script, dashboard card and history graph pointing at the old ids is now pointing
at nothing.

This integration already solves that for its **own** devices, live, through the
`shelly_cloud_diy.replace_device` service. Use that one where it applies.

This script solves it for devices owned by **another integration** — most
importantly the built-in, local Shelly integration, where most people's Shellys
actually live. It rewrites the device registry, the entity registry and the
config entry so the *old* Home Assistant device now points at the *new*
hardware. `entity_id` never changes, which is the whole point.

### Why it is offline, and why that is deliberate

Editing another integration's registries from inside a running Home Assistant is
the failure mode Home Assistant's own maintainers warned about when a version of
this was proposed for the core: silent, plausible, wrong writes that nobody
notices until an automation stops firing. So this runs with Home Assistant
**stopped**, prints exactly what it would change, writes only when you say so,
and backs up every file it touches first.

It is not a supported Home Assistant feature and it never will be. It is a
reviewed, reversible operation you perform once.

### Use it

```bash
# 1. Look. This is the default and it writes nothing at all.
python3 tools/replace_device_offline.py \
    --config /path/to/homeassistant/config \
    --old AABBCCDDEEFF --new 112233445566

# 2. Read the plan. Every entity it will touch is listed, with the id it keeps.

# 3. Stop Home Assistant, then apply.
python3 tools/replace_device_offline.py \
    --config /path/to/homeassistant/config \
    --old AABBCCDDEEFF --new 112233445566 \
    --ha-is-stopped --apply

# 4. Start Home Assistant and check one of the rewritten entities.
```

Useful flags:

| Flag | What for |
|---|---|
| `--domain` | The integration that owns the device. Defaults to `shelly` (the built-in one); pass `shelly_cloud_diy` to act on this integration's devices instead. |
| `--new-host` | The replacement's address, when the integration stores one and Home Assistant has not discovered the new unit yet. Without it the entry keeps the old address, and the script says so rather than leaving you to find out. |

### How it treats the files

`core.config_entries` holds the credentials of every integration you have, so
the writing is the part that got the most care:

- **Permissions are preserved exactly.** The replacement is created with the
  original's mode set at creation, not chmod'ed afterwards, so there is never a
  moment where a credential file is readable by anyone who could not read it
  before. (An earlier version of this script did not do that and silently
  turned an `0600` registry into `0664`.)
- **All three registries change together or not at all.** Each replacement is
  written and re-read first; the originals are swapped only once every one of
  them is known good. An interruption before that leaves your installation
  exactly as it was.
- **Backups first**, one per file, next to the original and with the same
  permissions. They are not cleaned up — they contain credentials, so delete
  them yourself once you are happy.
- **Values that look like secrets are not printed.** The plan tells you *that* a
  password-shaped field changes, never what to. The change is still applied.
- Files are written in Home Assistant's own compact shape, so a 3 MB entity
  registry does not balloon just because a tool passed through it.

Run it as the same user Home Assistant runs as.

### What it refuses to do

It refuses rather than half-doing, every time: an unparseable MAC, a MAC that
belongs to no device of that integration, the same MAC twice, an integration
that does not key its entities on the MAC (so the swap would change nothing),
and `--apply` without confirming Home Assistant is stopped — a running instance
holds these registries in memory and would overwrite the change on its next
save.

### What it does not do

It does not talk to any device, cannot tell whether the replacement is the same
model, and does not migrate history — the recorder keys on `entity_id`, which is
preserved, so history follows on its own.
