import json
import os
import time

import watchdog


def _touch(path, age, now, text="{}"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.utime(path, (now - age, now - age))


def _heartbeat(root, age, now, busy=None):
    _touch(os.path.join(root, "bridge", "heartbeat.json"), age, now,
           json.dumps({"project_name": "song.rpp", "busy": busy}))


def test_bridge_state_classes(tmp_path):
    root, now = str(tmp_path), time.time()
    assert watchdog.bridge_state(root, now)["state"] == "missing"
    _heartbeat(root, 3, now)
    assert watchdog.bridge_state(root, now)["state"] == "connected"
    _heartbeat(root, 120, now, busy="render")
    assert watchdog.bridge_state(root, now)["state"] == "busy"
    _heartbeat(root, 120, now)
    assert watchdog.bridge_state(root, now)["state"] == "stale"
    _heartbeat(root, 20 * 60, now, busy="render")
    assert watchdog.bridge_state(root, now)["state"] == "stale"


def test_sweep_retires_old_commands_and_replies_only(tmp_path):
    root, now = str(tmp_path), time.time()
    _touch(os.path.join(root, "inbox", "old.json"), 16 * 60, now)
    _touch(os.path.join(root, "inbox", "queued-behind-render.json"), 10 * 60, now)
    _touch(os.path.join(root, "outbox", "unread.json"), 2 * 3600, now)
    _touch(os.path.join(root, "outbox", "fresh.json"), 30, now)
    _touch(os.path.join(root, "outbox", "half.json.tmp"), 2 * 3600, now)
    _touch(os.path.join(root, "outbox", "notes.txt"), 9 * 3600, now)
    done = watchdog.sweep_queues(root, now)
    assert done == {"retired_commands": ["old.json"], "deleted_replies": ["unread.json"],
                    "deleted_tmp": ["half.json.tmp"]}
    # Retired commands are moved to failed/, never run and never lost.
    assert os.path.isfile(os.path.join(root, "failed", "old.json"))
    assert sorted(os.listdir(os.path.join(root, "inbox"))) == ["queued-behind-render.json"]
    assert sorted(os.listdir(os.path.join(root, "outbox"))) == ["fresh.json", "notes.txt"]


def test_sweep_dry_run_changes_nothing(tmp_path):
    root, now = str(tmp_path), time.time()
    _touch(os.path.join(root, "inbox", "old.json"), 16 * 60, now)
    _touch(os.path.join(root, "outbox", "unread.json"), 2 * 3600, now)
    done = watchdog.sweep_queues(root, now, dry_run=True)
    assert done["retired_commands"] == ["old.json"]
    assert os.path.isfile(os.path.join(root, "inbox", "old.json"))
    assert os.path.isfile(os.path.join(root, "outbox", "unread.json"))


def test_dismissal_never_answers_a_decision():
    def d(*labels):
        return {"buttons": [{"label": l, "id": i + 1} for i, l in enumerate(labels)]}
    assert watchdog.dismissal_for(d("Yes", "No", "&Cancel")) == ("cancel", 3)
    assert watchdog.dismissal_for(d("OK")) == ("close", None)
    assert watchdog.dismissal_for(d()) == ("close", None)
    assert watchdog.dismissal_for(d("Yes", "No")) is None
    assert watchdog.dismissal_for(d("Save", "Don't save")) is None
    assert watchdog.dismissal_for(d("Retry", "Ignore")) is None


def test_closed_reaper_is_reported_without_launch(tmp_path):
    r = watchdog.check(str(tmp_path), running=False, sweep=False,
                       launcher=lambda exe: (_ for _ in ()).throw(AssertionError("launched")))
    assert not r["ok"] and "--launch" in r["problems"][0]


def test_launch_respects_backoff_and_reports_missing_heartbeat(tmp_path):
    root, now, launched = str(tmp_path), time.time(), []
    r = watchdog.check(root, launch=True, running=False, sweep=False, now=now,
                       last_launch=now - 60, exe="reaper.exe", launcher=launched.append)
    assert launched == [] and "backoff" in r["problems"][0]
    r = watchdog.check(root, launch=True, running=False, sweep=False, now=now,
                       exe="reaper.exe", wait_after_launch=0,
                       launcher=lambda exe: launched.append(exe) or True)
    assert launched == ["reaper.exe"] and r["launched_at"] == now and r["ok"]


def test_running_with_dialog_and_stale_bridge(tmp_path):
    root, now = str(tmp_path), time.time()
    _heartbeat(root, 300, now)
    nag = {"hwnd": 1, "title": "Plugin", "text": "Activate now?", "buttons": [{"label": "Yes", "id": 6}]}
    r = watchdog.check(root, running=True, sweep=False, now=now, dialogs=[nag],
                       dismiss_dialogs=True)
    # A Yes-only popup is a decision: reported, never pressed.
    assert r["problems"] == ['REAPER shows dialog "Plugin": Activate now?']
    r = watchdog.check(root, running=True, sweep=False, now=now, dialogs=[])
    assert "may be frozen" in r["problems"][0] and "Not killing" in r["problems"][0]


def test_healthy_pass_is_ok(tmp_path):
    root, now = str(tmp_path), time.time()
    _heartbeat(root, 2, now)
    r = watchdog.check(root, running=True, now=now, dialogs=[])
    assert r["ok"] and r["actions"] == []
    assert watchdog.summarize(r).startswith("OK: REAPER running | bridge connected")


def test_dialog_beside_a_live_bridge_is_a_notice_not_a_problem(tmp_path):
    root, now = str(tmp_path), time.time()
    _heartbeat(root, 2, now)
    about = {"hwnd": 1, "title": "About REAPER", "text": "REAPER IS NOT FREE.",
             "buttons": [{"label": "Cancel", "id": 2}]}
    r = watchdog.check(root, running=True, sweep=False, now=now, dialogs=[about],
                       dismiss_dialogs=True)
    assert r["ok"] and r["actions"] == []
    assert r["notices"] == ['REAPER shows dialog "About REAPER": REAPER IS NOT FREE.']


def test_heartbeat_of_a_closed_reaper_is_not_live(tmp_path):
    root, now = str(tmp_path), time.time()
    _heartbeat(root, 5, now)  # written moments before REAPER quit
    r = watchdog.check(root, running=False, sweep=False, now=now)
    assert r["bridge"]["state"] == "stale"


def test_launch_waits_for_a_heartbeat_newer_than_the_launch(tmp_path, monkeypatch):
    root, now = str(tmp_path), time.time()
    _heartbeat(root, 5, now)  # left over from the REAPER that just closed
    monkeypatch.setattr(watchdog.time, "sleep", lambda s: None)
    clock = iter([now + i for i in range(0, 400, 3)])
    monkeypatch.setattr(watchdog, "_now", lambda: next(clock))
    state = watchdog._wait_for_heartbeat(root, 10, since=now)
    assert state["state"] != "connected"
