#!/usr/bin/env python3
"""Unattended supervision for the Reaper Daemon bridge, from OUTSIDE REAPER.

__startup.lua already restarts a dead bridge loop, but only while REAPER is
open and responsive. This covers what it cannot see:

  * REAPER closed       -> launch it (opt-in, with a relaunch backoff), then
                           wait for the bridge heartbeat.
  * REAPER blocked      -> a modal dialog (plugin popup, license nag, error box)
                           stops the defer loop. Windows: name the dialog, its
                           text and buttons. Dismissal is opt-in and only ever
                           presses Cancel (or closes an OK-only box); it never
                           answers Yes/No, so it cannot save or discard work.
  * stale commands      -> a command left in inbox/ while REAPER was down would
                           run later against whatever project is open then.
                           Past the age gate it moves to failed/ instead.
  * reply buildup       -> replies nobody read (fire-and-forget sends, crashed
                           callers) are deleted after a TTL, with stray .tmp.

It never kills REAPER: a frozen REAPER may hold unsaved work, so that state is
reported, not "fixed". Stdlib only.

    python watchdog.py                   # one check, report only
    python watchdog.py --launch          # also start REAPER if it is closed
    python watchdog.py --loop --launch   # keep supervising every 30 s
"""

import argparse
import datetime
import json
import os
import platform
import subprocess
import sys
import time

import reaperd

# A sender waits at most ~11 min (a 600 s capture plus 60 s headroom) and
# withdraws its command on timeout, so anything older than this in inbox/ has
# no one waiting and must not run. Matches the bridge's REQUEUE_MAX_AGE.
INBOX_MAX_AGE = 15 * 60
OUTBOX_TTL = 60 * 60          # replies are read within their sender's timeout
TMP_TTL = 60 * 60
FRESH_SECS = 15               # reaperd.status_ok's heartbeat freshness
BUSY_CAP = 15 * 60            # reaperd.status_ok's render-busy ceiling
LAUNCH_BACKOFF = 5 * 60       # never relaunch more often than this
LAUNCH_WAIT = 120             # how long to wait for a heartbeat after launch


def _now():
    return time.time()


# --- heartbeat --------------------------------------------------------------

def bridge_state(bridge_root, now=None):
    """Classify the heartbeat the way reaperd.status_ok does, as data.

    state: connected | busy | stale | missing.
    """
    now = _now() if now is None else now
    hb = os.path.join(bridge_root, "bridge", "heartbeat.json")
    if not os.path.isfile(hb):
        return {"state": "missing"}
    age = now - os.path.getmtime(hb)
    # A write racing the stat can put mtime a moment ahead of now.
    info = {"age": round(max(0.0, age), 1)}
    try:
        with open(hb, "r", encoding="utf-8") as f:
            d = json.load(f)
        info["project"] = d.get("project_name")
        info["busy"] = d.get("busy") or None
    except (OSError, ValueError):
        info["busy"] = None
    if age <= FRESH_SECS:
        info["state"] = "connected"
    elif info["busy"] and age <= BUSY_CAP:
        info["state"] = "busy"
    else:
        info["state"] = "stale"
    return info


# --- queue hygiene ------------------------------------------------------------

def _aged(path, now, max_age):
    try:
        return now - os.path.getmtime(path) > max_age
    except OSError:
        return False


def sweep_queues(bridge_root, now=None, inbox_max_age=INBOX_MAX_AGE,
                 outbox_ttl=OUTBOX_TTL, dry_run=False):
    """Retire stale commands and unread replies. Returns what it did."""
    now = _now() if now is None else now
    done = {"retired_commands": [], "deleted_replies": [], "deleted_tmp": []}
    inbox = os.path.join(bridge_root, "inbox")
    outbox = os.path.join(bridge_root, "outbox")
    failed = os.path.join(bridge_root, "failed")
    for folder in (inbox, outbox):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            if name.endswith(".tmp"):
                if _aged(path, now, TMP_TTL):
                    done["deleted_tmp"].append(name)
                    if not dry_run:
                        _remove(path)
            elif not name.endswith(".json"):
                continue
            elif folder == inbox and _aged(path, now, inbox_max_age):
                # Moved, not deleted: failed/ is where the bridge itself puts
                # commands it refused, and its own retention bounds that dir.
                done["retired_commands"].append(name)
                if not dry_run:
                    os.makedirs(failed, exist_ok=True)
                    try:
                        os.replace(path, os.path.join(failed, name))
                    except OSError:
                        done["retired_commands"].pop()
            elif folder == outbox and _aged(path, now, outbox_ttl):
                done["deleted_replies"].append(name)
                if not dry_run:
                    _remove(path)
    return done


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# --- REAPER process -------------------------------------------------------

def find_reaper_exe():
    """REAPER's executable, or None. REAPER_EXE wins when set."""
    env = os.environ.get("REAPER_EXE")
    if env:
        return env if os.path.exists(env) else None
    system = platform.system()
    if system == "Windows":
        candidates = []
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\REAPER") as k:
                        candidates.append(os.path.join(winreg.QueryValue(k, None), "reaper.exe"))
                except OSError:
                    pass
        except ImportError:
            pass
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            candidates += [os.path.join(base, "REAPER (x64)", "reaper.exe"),
                           os.path.join(base, "REAPER", "reaper.exe")]
        return next((c for c in candidates if os.path.isfile(c)), None)
    if system == "Darwin":
        app = "/Applications/REAPER.app"
        return app if os.path.isdir(app) else None
    for c in ("/usr/bin/reaper", "/usr/local/bin/reaper",
              os.path.expanduser("~/opt/REAPER/reaper")):
        if os.path.isfile(c):
            return c
    return None


def launch_reaper(exe):
    """Start REAPER detached from this process. Returns True if spawned."""
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", exe])
        elif platform.system() == "Windows":
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen([exe], creationflags=flags, close_fds=True,
                             cwd=os.path.dirname(exe))
        else:
            subprocess.Popen([exe], start_new_session=True)
        return True
    except OSError:
        return False


# --- modal dialogs (Windows) -------------------------------------------------

IDCANCEL = 2
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111


def _win_dialogs():
    """Visible standard dialogs (#32770) owned by a reaper.exe process.

    Each: {hwnd, title, text, buttons}. Plugin editor windows are not #32770
    and are not reported; message boxes, plugin license popups built on the
    Win32 dialog class, and REAPER's own prompts are.
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def text_of(hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def class_of(hwnd):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def exe_of(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            kernel32.CloseHandle(h)

    found = []

    def on_top(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and class_of(hwnd) == "#32770"
                and exe_of(hwnd) == "reaper.exe"):
            texts, buttons = [], []

            def on_child(child, _):
                cls, t = class_of(child), text_of(child).strip()
                if t and user32.IsWindowVisible(child):
                    if cls == "Button":
                        buttons.append({"label": t.replace("&", ""),
                                        "id": user32.GetDlgCtrlID(child)})
                    elif cls == "Static":
                        texts.append(t)
                return True
            user32.EnumChildWindows(hwnd, WNDENUMPROC(on_child), 0)
            found.append({"hwnd": int(hwnd), "title": text_of(hwnd),
                          "text": " ".join(texts)[:500], "buttons": buttons})
        return True

    user32.EnumWindows(WNDENUMPROC(on_top), 0)
    return found


def find_dialogs():
    """Blocking-dialog candidates, or None where this cannot be checked."""
    if platform.system() != "Windows":
        return None
    try:
        return _win_dialogs()
    except Exception:
        return None


def dismissal_for(dialog):
    """How to dismiss a dialog safely, or None to leave it for a person.

    Cancel when there is one. A box whose only button is OK is a notice, and
    closing it changes nothing. Anything else (Yes/No, Save/Don't save, custom
    choices) is a decision with consequences and is left alone.
    """
    def norm(label):
        return label.replace("&", "").strip().lower()
    labels = [norm(b["label"]) for b in dialog.get("buttons", [])]
    for b in dialog.get("buttons", []):
        if norm(b["label"]) == "cancel":
            return ("cancel", b["id"] or IDCANCEL)
    if labels in ([], ["ok"]):
        return ("close", None)
    return None


def dismiss(dialog):
    import ctypes
    plan = dismissal_for(dialog)
    if plan is None:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = dialog["hwnd"]
    if plan[0] == "cancel":
        return bool(user32.PostMessageW(hwnd, WM_COMMAND, plan[1], 0))
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


# --- one pass -----------------------------------------------------------------

def check(bridge_root, *, launch=False, dismiss_dialogs=False, sweep=True,
          last_launch=None, now=None, wait_after_launch=LAUNCH_WAIT,
          running=None, exe=None, launcher=launch_reaper, dialogs=None):
    """One supervision pass. Returns a report dict; never raises for state.

    `running`, `exe`, `launcher` and `dialogs` are injectable for tests.
    """
    now = _now() if now is None else now
    report = {"checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
              "actions": [], "problems": []}
    running = reaperd.reaper_running() if running is None else running
    report["reaper_running"] = running
    state = bridge_state(bridge_root, now)
    if running is False and state["state"] in ("connected", "busy"):
        # The file outlives the process by up to FRESH_SECS; a heartbeat
        # from a REAPER that is no longer running is not a live bridge.
        state = dict(state, state="stale")
    report["bridge"] = state

    if running is False:
        if not launch:
            report["problems"].append("REAPER is closed (run with --launch to start it)")
        elif last_launch is not None and now - last_launch < LAUNCH_BACKOFF:
            report["problems"].append(
                f"REAPER is closed; last launch {now - last_launch:.0f}s ago, "
                f"waiting out the {LAUNCH_BACKOFF}s backoff")
        else:
            exe = exe if exe is not None else find_reaper_exe()
            if not exe:
                report["problems"].append("REAPER is closed and its executable was "
                                          "not found (set REAPER_EXE)")
            elif launcher(exe):
                report["actions"].append(f"launched {exe}")
                report["launched_at"] = now
                if wait_after_launch:
                    report["bridge"] = _wait_for_heartbeat(bridge_root, wait_after_launch,
                                                           since=now)
                    if report["bridge"]["state"] != "connected":
                        report["problems"].append(
                            f"launched REAPER but no fresh bridge heartbeat within "
                            f"{wait_after_launch}s (startup popup, or bridge not installed)")
            else:
                report["problems"].append(f"could not start {exe}")

    if running is not False:
        found = find_dialogs() if dialogs is None else dialogs
        report["dialogs"] = found
        responsive = state["state"] in ("connected", "busy")
        for d in found or []:
            label = f"dialog \"{d['title']}\": {d['text'][:120]}"
            if responsive:
                # The bridge kept beating with this open, so it blocks nothing
                # (REAPER's About/evaluation box is like this). Listed, untouched.
                report.setdefault("notices", []).append("REAPER shows " + label)
            elif dismiss_dialogs and dismissal_for(d) and dismiss(d):
                report["actions"].append("dismissed " + label)
            else:
                report["problems"].append("REAPER shows " + label)
        if state["state"] == "stale" and not found:
            report["problems"].append(
                "REAPER is running but the bridge heartbeat is stale and no dialog "
                "was found; REAPER may be frozen or the bridge stopped. Not killing "
                "REAPER: it may hold unsaved work.")
        elif state["state"] == "missing":
            report["problems"].append("REAPER is running but the bridge never wrote a "
                                      "heartbeat (python setup/install.py, then relaunch)")

    if sweep:
        swept = sweep_queues(bridge_root, now)
        for key, verb in (("retired_commands", "moved stale command to failed/"),
                          ("deleted_replies", "deleted unread reply"),
                          ("deleted_tmp", "deleted stray temp file")):
            for name in swept[key]:
                report["actions"].append(f"{verb}: {name}")
    report["ok"] = not report["problems"]
    return report


def _wait_for_heartbeat(bridge_root, seconds, since):
    """Wait for a heartbeat written AFTER `since` (the launch). One left over
    from the previous REAPER can still look fresh for a few seconds."""
    hb = os.path.join(bridge_root, "bridge", "heartbeat.json")
    deadline = _now() + seconds
    state = {"state": "missing"}
    while _now() < deadline:
        state = bridge_state(bridge_root)
        try:
            new = os.path.getmtime(hb) > since
        except OSError:
            new = False
        if state["state"] == "connected" and new:
            return state
        if state["state"] == "connected":
            state = dict(state, state="stale")
        time.sleep(2)
    return state


def _log(bridge_root, report):
    os.makedirs(os.path.join(bridge_root, "logs"), exist_ok=True)
    path = os.path.join(bridge_root, "logs", "watchdog.log")
    try:
        if os.path.getsize(path) > 1024 * 1024:
            os.replace(path, path + ".1")
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(report, separators=(",", ":")) + "\n")


def summarize(report):
    b = report["bridge"]
    rr = report["reaper_running"]
    proc = ("relaunched" if report.get("launched_at") else
            "running" if rr else "closed" if rr is False else "unknown")
    head = (f"REAPER {proc}"
            f" | bridge {b['state']}" + (f" ({b['age']}s)" if "age" in b else ""))
    lines = [("OK: " if report["ok"] else "PROBLEM: ") + head]
    lines += ["  did: " + a for a in report["actions"]]
    lines += ["  problem: " + p for p in report["problems"]]
    lines += ["  note: " + n for n in report.get("notices", [])]
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--bridge-root", default=reaperd.BRIDGE_ROOT)
    p.add_argument("--launch", action="store_true", help="start REAPER if it is closed")
    p.add_argument("--dismiss-dialogs", action="store_true",
                   help="press Cancel on (or close OK-only) REAPER dialogs; never Yes/No")
    p.add_argument("--no-sweep", action="store_true", help="leave inbox/outbox alone")
    p.add_argument("--loop", action="store_true", help="keep checking")
    p.add_argument("--interval", type=float, default=30, help="seconds between loop checks")
    p.add_argument("--json", action="store_true", help="print the raw report")
    args = p.parse_args(argv)

    last_launch = None
    while True:
        report = check(args.bridge_root, launch=args.launch,
                       dismiss_dialogs=args.dismiss_dialogs, sweep=not args.no_sweep,
                       last_launch=last_launch)
        last_launch = report.get("launched_at", last_launch)
        if args.loop:
            _log(args.bridge_root, report)
        print(json.dumps(report, indent=1) if args.json else summarize(report), flush=True)
        if not args.loop:
            return 0 if report["ok"] else 1
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    sys.exit(main())
