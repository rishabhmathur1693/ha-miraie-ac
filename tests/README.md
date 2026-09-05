# Local regression tests

Install `miraie-ac==1.1.2` into an isolated Python environment, then run:

```sh
python -m unittest discover -s tests -v
```

Connection tests use the real pinned library with simulated HTTP and MQTT.
Lifecycle and flow tests substitute Home Assistant boundaries. They verify
our logic, not Home Assistant's platform loader, scheduler, schema validation,
or UI. Full Core 2026.9.0 compatibility and physical AC testing remain required.

No test connects to Panasonic or requires account credentials.

## Real Home Assistant runtime checks

In a separate environment with Python >=3.14.2, install
`homeassistant==2026.9.0`, `miraie-ac==1.1.2`, `pytest`, and `pytest-asyncio`.
Run from the repository root:

```sh
python -m pytest tests/ha_runtime -v
```

These tests use actual HA platform loading, config entries, registries, entity
states, reauthentication, reload, unload and shutdown. Panasonic IO is simulated.
They also cover in-flight energy polling cancellation and reporting-period reset
boundaries. They do not prove live cloud or hardware compatibility.
