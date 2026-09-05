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
