# Watchdog

`watchdog.py` supervises the bridge from outside REAPER. REAPER's
`__startup.lua` already reloads a dead bridge loop, but only while REAPER is
open and responsive. The watchdog covers what that loop can't see.

```powershell
python watchdog.py                    # one check, report only
python watchdog.py --launch           # also start REAPER if it is closed
python watchdog.py --loop --launch    # keep checking every 30 s, log to logs/watchdog.log
```

Exit code 0 means no problems. 1 means at least one problem was reported.
`--json` prints the full report.

## What one check does

| Situation | Default | With a flag |
| --- | --- | --- |
| REAPER closed | Reported | `--launch` starts it, waits up to 120 s for a bridge heartbeat, and won't relaunch again within 5 minutes |
| A REAPER dialog is open and the bridge has stopped responding | Reported with title, text and buttons (Windows) | `--dismiss-dialogs` presses Cancel, or closes a box whose only button is OK |
| A dialog is open but the bridge is still responding | Listed as a note and left alone | Same |
| REAPER running, bridge stale, no dialog | Reported as possibly frozen | Never killed, since REAPER may hold unsaved work |
| Command in `inbox/` older than 15 minutes | Moved to `failed/`, so it can't run later against whatever project is open by then | `--no-sweep` skips this |
| Reply in `outbox/` older than 1 hour, or a stray `.tmp` file | Deleted | `--no-sweep` skips this |

Dismissal never answers a question. A dialog offering Yes/No, Save/Don't save
or any other choice stays open for a person. REAPER's evaluation-license box
("Still Evaluating") doesn't stop the bridge, so it's only listed.

Set `REAPER_EXE` if REAPER isn't installed in the default location. Dialog
detection is Windows-only. On macOS and Linux the other checks still run.
