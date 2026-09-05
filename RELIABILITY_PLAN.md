# MirAIe AC reliability implementation and device testing plan

Status: first lifecycle fixes in development; no pull request yet.

Implemented for development testing: entry-owned session cleanup, MQTT task
cancellation, energy timer unsubscription, login HTTP error classification,
password-only reauthentication, bounded MQTT reconnect delays, and malformed
message isolation. Small connection adapters retain the pinned library's
device command implementations. See `tests/README.md` for test scope.

Added in the next development batch: guarded MQTT publishes with Home Assistant
action errors; REST status refresh on connection/reconnection with per-AC error
isolation; bounded energy requests and rejection of invalid energy values.
Failed energy updates preserve the last reading/reset timestamp and mark the
sensor unavailable until a successful update. Failed commands are not replayed.

Remaining before a device test build: full HA runtime verification, connection
availability propagation, periodic REST reconciliation, diagnostics, and
packaging/rollback instructions. Energy reset-boundary behavior also needs
coverage. REST reconciliation currently runs at startup and MQTT connection;
it is not a periodic fallback during a sustained broker outage.
OAuth remains a separate feasibility investigation. Login rejection currently
recognizes HTTP 401/403; other server-specific credential error formats require
evidence before mapping them to reauthentication.

Target installation: Home Assistant OS, Core 2026.9.0, Supervisor 2026.08.0,
OS 18.2, Frontend 20260826.4. Currently using `ha-panasonic-miraie` via HACS.
Test ACs: CS-CU-NU18ZKY5W and CS-CU-HU18CKY5XFMH-P.
Keep the current integration available for rollback; disable its config entry
when testing the replacement. Map old/new entity IDs explicitly for automations.

Fork: https://github.com/rishabhmathur1693/ha-miraie-ac
Branch: `feat/reliability-hardening`
Baseline: `18ef085` (integration 1.1.8), dependency `miraie-ac==1.1.2`.

## Scope and findings

Keep the existing AC controls, entity identities, display switch, and energy sensors. Develop in small commits, test on the owner's two Panasonic ACs, fix observed issues, and repeat testing for several days after the last meaningful fix. Open a PR only when the owner explicitly requests it after testing.

Source inspection of the pinned PyPI library corrects the initial comparison: MQTT already retries every five seconds and calls `get_token()` after MQTT errors. A coordinator is an implementation option, not a reliability benefit by itself. Do not introduce a second reconnect loop.

Confirmed source-level concerns (not yet reproduced on hardware):

- Integration setup leaves `async with MirAIeHub()` immediately after initialization; the library's context exit closes the HTTP session still needed by authentication and energy requests.
- Energy entities individually reopen and close this shared session, creating unclear ownership and potential interference between entities.
- Unload removes the hub from Home Assistant storage without explicitly cancelling and awaiting its broker background tasks.
- Config validation labels every authentication-call exception as invalid credentials, including network failures.
- Library `get_token()` suppresses all errors and returns the old token; the MQTT loop handles MQTT errors but malformed JSON or an unknown-topic callback can terminate its listener.
- Library status fetching gathers exceptions but subsequently indexes every result as a dictionary, so one failed device request can break the combined update.

## Implementation sequence

### 1. Baseline and regression coverage

Record Home Assistant version, installation method, both AC model numbers, and current working features. Add focused tests around setup, failed setup, unload, authentication errors, and two-device isolation. Establish the supported Home Assistant/Python test versions and CI using current official documentation before implementation.

### 2. Session and task ownership

Give the config entry one runtime owner for the hub, HTTP session, and background tasks. Keep the session open for the entry lifetime. Close resources once after successful platform unload, on failed setup, and on shutdown as appropriate. Remove session ownership from energy entities. Cancel and await broker tasks; verify repeated reloads do not leave duplicate listeners or polling callbacks.

Acceptance: reload repeatedly without session-closed failures, leaked sessions, duplicate broker tasks, or cross-device sensor interference.

### 3. Error classification and reauthentication

Separate temporary connection errors from invalid credentials. Use Home Assistant setup retry for transient failures and a reauthentication flow for verified credential rejection. Preserve config-entry and entity identities when credentials change. Avoid treating every HTTP error as an authentication error.

The library currently raises generic authentication exceptions; identify the smallest supported adapter or dependency change required to retain HTTP error detail. Do not classify errors by fragile string matching.

### 4. MQTT and state recovery

Harden the existing broker loop with bounded backoff and jitter, connection-state reporting, malformed-message isolation, and deliberate cancellation. Resubscribe after reconnect and reconcile AC state. Clear disconnected client references. Ensure one AC's malformed state or failed request does not stop updates for the other.

Add bounded, low-frequency REST status reconciliation where the pinned API supports it. Silence alone is not proof of failure: idle ACs may emit no messages. Separate device-offline status from a cloud-connection outage. Verify partial updates and availability behavior.

Protocol changes may belong in the Python library. Decide whether a small integration adapter is sufficient or a separate library fork/release is needed before introducing a dependency change. Do not silently depend on an unmaintainable copy of the entire library.

### 5. Commands, energy, and diagnostics

Report disconnected-client and publish failures through useful Home Assistant errors. Evaluate confirmation using returned device state with a bounded timeout; MQTT publish acceptance is not device confirmation. Account for repeated identical commands and late messages. Do not automatically replay stale commands after reconnect.

Keep energy polling errors isolated, preserve valid readings appropriately, and test daily/weekly/monthly reset boundaries. Add redacted diagnostics and useful connection logging without passwords, tokens, home identifiers, or MQTT topics.

REST command fallback is conditional on verifying the AC library's auth/API contract; Panasonic OAuth endpoints are not assumed interchangeable with this library's API.

### 6. OAuth feasibility, separately

Validate an appropriate Panasonic client registration, scopes, token renewal, API compatibility, and migration path. Do not copy another integration's embedded client secret. Keep OAuth out of the first test build unless these requirements are resolved. Existing credential-based reauthentication can improve reliability independently.

## Two-AC testing

Back up Home Assistant and record the original installed version before installing a test build. Provide precise installation and rollback instructions for the user's actual installation method. A Git branch is not assumed to appear in HACS version selection; choose a supported test distribution method when packaging the first build.

For each AC, verify power, temperature, modes, fan speeds, both swing axes, presets/Converti supported by that model, display switch, automations, and energy values against the MirAIe app. Exercise both ACs together and change settings through the app/remote as well as Home Assistant.

Test integration reload and Home Assistant restart, one AC offline while the other remains available, brief internet loss followed by recovery, and prolonged idle operation. Check that recovery needs no manual reload and affects no unrelated entities. Credential-change testing is optional and should be coordinated with the owner because it affects the mobile app too.

Log date/time and timezone, build commit, AC model, action, expected/actual behavior, recovery behavior, and redacted logs for each issue. Never include account credentials.

After fixes, rerun the affected scenarios and run both ACs normally for several more days (target 5–7 days after the last meaningful fix). Include overnight operation and at least one natural daily energy update. Automated tests should cover reset boundaries that the observation window cannot reach.

## PR gate

Require passing automated checks, successful two-device testing, reviewed diagnostics redaction, stable entity identities, rollback instructions, and a documented observation period. Review the diff and dependency strategy before packaging. Prepare a PR only after the owner explicitly approves; do not open even a draft PR during testing.
