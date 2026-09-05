# Two-AC beta testing: 1.1.9b1

This is a development build for controlled testing, not a stable release.
Target: Home Assistant Core 2026.9.0 on HA OS 18.2.
Devices: CS-CU-NU18ZKY5W and CS-CU-HU18CKY5XFMH-P.

## Before installation

Create and download a Home Assistant backup. Record the current Panasonic
integration version and the entity IDs used in dashboards and automations.
Keep `ha-panasonic-miraie` installed in HACS for rollback.

When ready to switch, disable its config entry in Settings → Devices & services.
Do not delete it. The replacement uses the `miraie` domain, so it creates new
devices and entities. Existing Panasonic-domain automations do not migrate
automatically. Initially test the new entities manually.

## HACS beta installation

1. In HACS, open **Integrations**, use the top-right menu, and select
   **Custom repositories**.
2. Add `https://github.com/rishabhmathur1693/ha-miraie-ac` with category
   **Integration**.
3. Open the resulting **MirAIe** repository and choose **Download**. Select
   version `v1.1.9b1` if HACS asks for a version. It is a prerelease, so use
   **Show beta versions** in the download dialog if necessary.
4. Restart Home Assistant when HACS requests it.

HACS stores the integration under `/config/custom_components/miraie`. It does
not overwrite `/config/custom_components/panasonic_cloud`, because the two
integrations use different domains.

Restart Home Assistant. In Settings → Devices & services → Add integration,
choose **MirAIe** and enter your MirAIe app account credentials. Phone usernames
include the country code. This build still uses username/password authentication;
OAuth is not included. Do not send credentials in bug reports or chat.

Keep the fork's HACS repository selected during testing. Installing the upstream
`rkzofficial` repository would replace the beta's `miraie` directory.

## First-session checks

For each AC, record new entity IDs and verify:

- Power, set temperature, HVAC mode and fan speed.
- Vertical/horizontal swing, supported presets and Converti settings.
- AC display switch and state changes made through the MirAIe app/remote.
- Daily, weekly and monthly energy sensors. These are cloud-reported period
  totals, not live instantaneous power; delayed or missing data is possible.

Only test features supported by the particular AC. Compare actual behavior with
the app; an exposed option does not prove the hardware supports it.

Check integration reload and HA restart. There should still be two climate
entities, two display switches and six energy sensors without duplicates.
If satisfactory, update the relevant automations/dashboard references explicitly.

## Recovery checks

Test one AC offline while the other works, then restore it. Test a brief internet
outage during a convenient period. MQTT-dependent controls should become
unavailable and recover automatically. Failed commands are not queued or replayed.
State refresh runs on MQTT connection and every 15 minutes; periodic reads cannot
restore command control while MQTT is unavailable.

Verify the system after overnight idle time. Password-change testing is optional:
coordinate it because changing the account password also affects the mobile app.
HTTP 401/403 login rejection should offer reauthentication without changing the
existing entry's entity IDs.

## Observation and bug reports

After each fix, repeat affected checks and observe normal use for 5–7 days after
the final meaningful change. Include at least one natural daily energy update.

Record build version/commit, timestamp (Asia/Kolkata), AC model, action,
expected/actual result, and whether recovery required a reload. The integration's
diagnostics export contains aggregate counters rather than raw account/device
data. Review any separately collected logs before sharing them: the underlying
library can log device information at debug level.

## Rollback

Disable the new **MirAIe** config entry. Re-enable the original **Panasonic
MirAIe** entry, and restore any automation/dashboard references you changed.
Keep both integrations' files initially; do not delete the original entry.
If the beta prevents startup, remove only the newly installed
`/config/custom_components/miraie` folder using your existing file-access method,
then restart, or restore the pre-test backup.

No upstream PR is planned until the owner completes testing and explicitly
approves one. OAuth and device-level command confirmation remain deferred.
