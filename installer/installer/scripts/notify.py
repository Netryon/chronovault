#!/usr/bin/env python3
"""
Chronovault notifier (SMTP email)

Reads Chronovault status + local system signals and sends email alerts.
Keeps a small state file to avoid spamming the same alert repeatedly.

Selected alerts (7 total):
- A1: Backup not OK (persistent)
- A2: Backup recovered (transition-only)
- C3_primary: Low space on primary mount >= 90% (persistent)
- C3_backup: Low space on backup mount >= 90% (persistent)
- E1: Mirror not OK (persistent)
- E4: Mirror recovered (transition-only)
- G1: Control API service down (persistent)
- G2: Backup timer issue (persistent)

Paths assume your Chronovault layout:
- /var/lib/chronovault/status.json
- /var/lib/chronovault/state/notify_state.json (created)

Manual testing:
  --test <alert_id>        Force trigger a specific alert (e.g., --test A1)
  --test-all               Force trigger all alerts once
  --simulate <condition>   Simulate conditions in-memory (does not modify status.json)
  --status-file <path>     Use alternative status.json file for testing
  --dry-run                Show what would fire without sending emails
  --commit-state           Write state file even in test mode (default: test mode doesn't write state)
  --force                  Bypass initial run guard and suppression
  --list-alerts            List all available alert IDs
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from mailer import send_email
except ImportError:
    print("ERROR: mailer module not found. Ensure mailer.py is in the same directory.", file=sys.stderr)
    sys.exit(1)

# =========================
# Config (env) - using control.env naming convention ONLY
# =========================
CHRONO_NAME = os.environ.get("CHRONOVAULT_NAME", "Chronovault")

STATUS_PATH = os.environ.get("CHRONOVAULT_STATUS_PATH", "/var/lib/chronovault/status.json")
STATE_DIR = os.environ.get("CHRONOVAULT_STATE_DIR", "/var/lib/chronovault/state")
NOTIFY_STATE_PATH = os.path.join(STATE_DIR, "notify_state.json")

PRIMARY_MOUNT = os.environ.get("CHRONOVAULT_PRIMARY_MOUNT", "/mnt/primary")
BACKUP_MOUNT = os.environ.get("CHRONOVAULT_BACKUP_MOUNT", "/mnt/backup")

# Using control.env naming convention with fallback to old names for compatibility
DISK_USED_WARN_PCT = float(os.environ.get("CHRONOVAULT_DISK_USED_WARN_PCT") or os.environ.get("CHRONOVAULT_LOW_SPACE_WARN_PCT", "90"))

# Note: D thresholds removed (D alerts not included in simplified version)

# G checks
CONTROL_SERVICE = os.environ.get("CHRONOVAULT_CONTROL_SERVICE", "chronovault-control.service")
BACKUP_TIMER = os.environ.get("CHRONOVAULT_BACKUP_TIMER", "chronovault-backup.timer")

# Rate limiting (default: resend persistent alerts every 12 hours max)
# Support both new and old naming for compatibility
PERSISTENT_REPEAT_SEC = int(os.environ.get("CHRONOVAULT_PERSISTENT_REPEAT_SEC") or os.environ.get("CHRONOVAULT_PERSISTENT_RESEND_SEC", str(12 * 3600)))

# =========================
# Alert definitions for manual testing
# Only 7 alerts total:
# - A1: Backup not OK (persistent)
# - A2: Backup recovered (transition-only)
# - C3_primary: Low space on primary (persistent)
# - C3_backup: Low space on backup (persistent)
# - E1: Mirror not OK (persistent)
# - E4: Mirror recovered (transition-only)
# - G1: Control API service down (persistent)
# - G2: Backup timer issue (persistent)
# =========================
ALL_ALERTS = [
    "A1", "A2",  # Backup status
    "C3_primary", "C3_backup",  # Low space (Primary + Backup)
    "E1", "E4",  # Mirror/sync
    "G1", "G2",  # System health
]

# =========================
# Helpers: JSON + state
# =========================
def _now() -> float:
    return time.time()

def _load_json(path: str, max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """
    Load JSON file with retry logic to handle race conditions during writes.
    Returns None on any error after all retries.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return None  # File doesn't exist - not an error, just return None
        except json.JSONDecodeError as e:
            # JSON decode error might be due to partial write - retry
            if attempt < max_retries - 1:
                import time
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                continue
            print(f"WARNING: Invalid JSON in {path} after {max_retries} attempts: {e}", file=sys.stderr)
            return None
        except (IOError, OSError, PermissionError) as e:
            # I/O errors might be temporary (file being written) - retry
            last_error = e
            if attempt < max_retries - 1:
                import time
                time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
                continue
            # Final attempt failed
            print(f"WARNING: Error reading {path} after {max_retries} attempts: {e}", file=sys.stderr)
            return None
        except Exception as e:
            # Other errors - don't retry
            print(f"WARNING: Unexpected error reading {path}: {e}", file=sys.stderr)
            return None
    
    # Should not reach here, but just in case
    if last_error:
        print(f"WARNING: Failed to read {path} after {max_retries} attempts: {last_error}", file=sys.stderr)
    return None

def _ensure_dir(path: str) -> None:
    """Ensure directory exists."""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        print(f"WARNING: Could not create directory {path}: {e}", file=sys.stderr)

def _write_json_atomic(path: str, data: Dict[str, Any]) -> None:
    """Write JSON file atomically."""
    _ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception as e:
        print(f"ERROR: Failed to write state file {path}: {e}", file=sys.stderr)
        # Try to clean up temp file
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def _key(k: str) -> str:
    """Namespace keys by project."""
    return k

def _get_sig(state: Dict[str, Any], key: str) -> Optional[str]:
    """Get signature from state."""
    v = state.get(key)
    if not isinstance(v, dict):
        return None
    s = v.get("signature")
    return s if isinstance(s, str) else None

def _get_last_sent(state: Dict[str, Any], key: str) -> Optional[float]:
    """Get last sent timestamp from state."""
    v = state.get(key)
    if not isinstance(v, dict):
        return None
    ts = v.get("last_sent_ts")
    return float(ts) if isinstance(ts, (int, float)) else None

def _get_last_seen(state: Dict[str, Any], key: str) -> Optional[str]:
    """Get last seen value for transition detection."""
    v = state.get(key)
    if not isinstance(v, dict):
        return None
    seen = v.get("last_seen")
    return seen if isinstance(seen, str) else None

def _mark_sent(state: Dict[str, Any], key: str, sig: str, last_seen: Optional[str] = None) -> None:
    """Mark alert as sent in state. Also store last_seen for transitions."""
    entry = {"last_sent_ts": _now(), "signature": sig}
    if last_seen is not None:
        entry["last_seen"] = last_seen
    state[key] = entry

def _mark_seen(state: Dict[str, Any], key: str, seen_value: str) -> None:
    """Mark value as seen without sending (for transition tracking)."""
    if key not in state:
        state[key] = {}
    state[key]["last_seen"] = seen_value

def _should_send_persistent(state: Dict[str, Any], key: str, sig: str, force: bool = False) -> bool:
    """
    Persistent alerts: send when signature changes OR resend every PERSISTENT_REPEAT_SEC.
    """
    if force:
        return True
    prev_sig = _get_sig(state, key)
    if prev_sig != sig:
        return True
    last = _get_last_sent(state, key)
    if last is None:
        return True
    return (_now() - last) >= PERSISTENT_REPEAT_SEC

def _should_send_transition(state: Dict[str, Any], key: str, current_value: str, force: bool = False) -> bool:
    """
    Transition alerts: send only when state flips one way (non-OK→OK, frozen→unfrozen, etc).
    No periodic resend.
    """
    if force:
        return True
    last_seen = _get_last_seen(state, key)
    # If no previous value recorded, this is first run - don't send transition
    if last_seen is None:
        return False
    # Only send if value changed
    return last_seen != current_value

def _send(subject: str, body: str, dry_run: bool = False) -> Dict[str, Any]:
    """Send email notification (or preview in dry-run mode)."""
    full_subject = f"{CHRONO_NAME}: {subject}"
    if dry_run:
        print(f"\n[DRY-RUN] Would send email:", file=sys.stderr)
        print(f"  Subject: {full_subject}", file=sys.stderr)
        print(f"  Body:\n{body}", file=sys.stderr)
        return {"ok": True, "dry_run": True}
    try:
        return send_email(subject=full_subject, body=body)
    except Exception as e:
        print(f"ERROR: Failed to send email: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}

# =========================
# Helpers: system checks
# =========================
def _run(cmd: List[str]) -> Tuple[int, str, str]:
    """Run command and return (returncode, stdout, stderr)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def _systemd_is_active(unit: str) -> Optional[bool]:
    """Check if systemd unit is active. Returns True/False/None."""
    rc, out, _ = _run(["systemctl", "is-active", unit])
    if rc == 0 and out == "active":
        return True
    if out in ("inactive", "failed", "deactivating", "activating"):
        return False
    return None

def _systemd_is_enabled(unit: str) -> Optional[bool]:
    """Check if systemd unit is enabled. Returns True/False/None."""
    rc, out, _ = _run(["systemctl", "is-enabled", unit])
    if rc == 0 and out == "enabled":
        return True
    if out in ("disabled", "static", "indirect", "masked", "generated", "transient"):
        return False
    return None

def _mount_rw(mountpoint: str) -> Optional[bool]:
    """Check mount state. Returns True if rw, False if ro, None if not mounted/unknown."""
    rc, out, _ = _run(["findmnt", "-no", "OPTIONS", mountpoint])
    if rc != 0 or not out:
        return None
    opts = out.split(",")
    if "rw" in opts:
        return True
    if "ro" in opts:
        return False
    return None

def _usage_pct(path: str) -> Optional[float]:
    """Get disk usage percentage. Returns None on error."""
    try:
        u = shutil.disk_usage(path)
        if u.total <= 0:
            return None
        return (u.used / u.total) * 100.0
    except Exception:
        return None

# =========================
# Test mode simulation (in-memory only, does not modify files)
# =========================
def _simulate_status(status: Dict[str, Any], test_mode: str) -> Dict[str, Any]:
    """Simulate conditions for testing. Returns modified copy, does not modify original."""
    simulated = status.copy()
    
    if test_mode == "simulate-error":
        simulated["state"] = "ERROR"
        simulated["reason"] = "Simulated error for testing"
    elif test_mode == "simulate-warn":
        simulated["state"] = "WARN"
        simulated["reason"] = "Simulated warning for testing"
    elif test_mode == "simulate-recovery":
        # Set state to OK to trigger A2 (backup recovered)
        simulated["state"] = "OK"
        simulated["reason"] = "Simulated recovery"
    elif test_mode == "simulate-mirror-error":
        simulated["mirror_state"] = "ERROR"
        simulated["mirror_reason"] = "Simulated mirror error"
        simulated["mirror_rsync_exit_code"] = 1
    elif test_mode == "simulate-mirror-recovery":
        # Set mirror_state to OK to trigger E4 (mirror recovered)
        simulated["mirror_state"] = "OK"
        simulated["mirror_reason"] = "Simulated mirror recovery"
        simulated["mirror_rsync_exit_code"] = 0
    
    return simulated

# =========================
# Notification functions for each alert
# =========================
def _check_a1_backup_not_ok(notify_state: Dict[str, Any], status: Dict[str, Any], force: bool = False, dry_run: bool = False) -> Tuple[bool, int, int]:
    """A1: Backup not OK (persistent)."""
    state = str(status.get("state", "UNKNOWN"))
    reason = str(status.get("reason", ""))
    
    # If forcing (test mode), always trigger with test values
    if force:
        state = "ERROR"
        reason = "Test alert - forced trigger"
    elif state == "OK" or state == "RUNNING":
        # RUNNING state means backup is actively in progress - not an error
        return False, 0, 0
    
    check_count = 1
    key = _key("A1_backup_not_ok")
    sig = f"state:{state}|reason:{reason}"
    
    # When forcing, always send (bypass all checks)
    should_send = force or _should_send_persistent(notify_state, key, sig, force=force)
    
    if should_send:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"""Backup Status Alert

Current Status: {state}
Reason: {reason if reason else "No reason provided"}

Timestamp: {timestamp}

Action Required:
- Check backup logs for details
- Verify backup storage is accessible
- Review system status

This alert will repeat every {PERSISTENT_REPEAT_SEC // 3600} hours while the condition persists.
"""
        res = _send("Backup Status: NOT OK", body, dry_run=dry_run)
        if res.get("ok"):
            _mark_sent(notify_state, key, sig)
            return True, 1, 0
        elif force:
            # In test mode, return success even if email failed (for testing purposes)
            _mark_sent(notify_state, key, sig)
            print(f"NOTE: Email send failed but test mode forced: {res.get('error', res.get('reason', 'unknown'))}", file=sys.stderr)
            return True, 1, 0
    
    return False, 0, 1

def _check_a2_backup_recovered(notify_state: Dict[str, Any], status: Dict[str, Any], force: bool = False, dry_run: bool = False) -> Tuple[bool, int, int]:
    """A2: Backup recovered to OK (transition only)."""
    state = str(status.get("state", "UNKNOWN"))
    reason = str(status.get("reason", ""))
    
    if state != "OK" and state != "RUNNING":
        # Only record ERROR/WARNING states for recovery detection
        # RUNNING is a normal transitional state, not an error state
        key = _key("A2_backup_ok")
        _mark_seen(notify_state, key, state)
        return False, 0, 0
    
    check_count = 1
    key = _key("A2_backup_ok")
    current_value = "OK"
    
    # Transition: only send if it was previously non-OK
    if _should_send_transition(notify_state, key, current_value, force=force):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"""Backup Recovery Notification

Backup status has recovered to OK.

Current Status: OK
Reason: {reason if reason else "No reason provided"}

Timestamp: {timestamp}

The backup system is now operating normally.
"""
        res = _send("Backup Status: Recovered to OK", body, dry_run=dry_run)
        if res.get("ok"):
            _mark_sent(notify_state, key, f"OK|reason:{reason}", last_seen=current_value)
            return True, 1, 0
    
    # Always record current state
    _mark_seen(notify_state, key, current_value)
    return False, 0, 1

def _check_e4_mirror_recovered(notify_state: Dict[str, Any], status: Dict[str, Any], force: bool = False, dry_run: bool = False) -> Tuple[bool, int, int]:
    """E4: Mirror recovered to OK (transition only)."""
    mirror_state = str(status.get("mirror_state", "UNKNOWN"))
    mirror_reason = str(status.get("mirror_reason", ""))
    
    if mirror_state != "OK":
        # Record current state for transition detection
        key = _key("E4_mirror_ok")
        _mark_seen(notify_state, key, mirror_state)
        return False, 0, 0
    
    check_count = 1
    key = _key("E4_mirror_ok")
    current_value = "OK"
    
    # Transition: only send if it was previously non-OK
    if _should_send_transition(notify_state, key, current_value, force=force):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"""Mirror Recovery Notification

Mirror status has recovered to OK.

Current Status: OK
Reason: {mirror_reason if mirror_reason else "No reason provided"}

Timestamp: {timestamp}

The mirror/sync system is now operating normally.
"""
        res = _send("Mirror Status: Recovered to OK", body, dry_run=dry_run)
        if res.get("ok"):
            _mark_sent(notify_state, key, f"OK|reason:{mirror_reason}", last_seen=current_value)
            return True, 1, 0
    
    # Always record current state
    _mark_seen(notify_state, key, current_value)
    return False, 0, 1

def _check_c3_low_space(notify_state: Dict[str, Any], label: str, mountpoint: str, force: bool = False, dry_run: bool = False) -> Tuple[bool, int, int]:
    """C3: Low space warning (persistent)."""
    pct = _usage_pct(mountpoint)
    
    # If forcing (test mode), use test value
    if force and (pct is None or pct < DISK_USED_WARN_PCT):
        pct = DISK_USED_WARN_PCT + 5.0
    
    if pct is None or pct < DISK_USED_WARN_PCT:
        return False, 0, 0
    
    check_count = 1
    key = _key(f"C3_low_space_{label.lower()}")
    # Use growth band in signature to prevent repeated sends for tiny variations
    band = int(pct // 5) * 5  # Round to nearest 5%
    sig = f"{band:.0f}"
    
    # When forcing, always send (bypass all checks)
    should_send = force or _should_send_persistent(notify_state, key, sig, force=force)
    
    if should_send:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"""Storage Alert: Low Disk Space

{label} storage is running low on space.

Mount Point: {mountpoint}
Current Usage: {pct:.1f}%
Warning Threshold: {DISK_USED_WARN_PCT:.0f}%

Timestamp: {timestamp}

Action Required:
- Review and clean up unnecessary files
- Consider expanding storage capacity
- Check for large files or directories
- Monitor space usage closely

This alert will repeat every {PERSISTENT_REPEAT_SEC // 3600} hours while usage remains above threshold.
"""
        res = _send(f"Storage Alert: Low Space on {label}", body, dry_run=dry_run)
        if res.get("ok"):
            _mark_sent(notify_state, key, sig)
            return True, 1, 0
        elif force:
            # In test mode, return success even if email failed (for testing purposes)
            _mark_sent(notify_state, key, sig)
            print(f"NOTE: Email send failed but test mode forced: {res.get('error', res.get('reason', 'unknown'))}", file=sys.stderr)
            return True, 1, 0
    
    return False, 0, 1

def _check_e1_mirror_not_ok(notify_state: Dict[str, Any], status: Dict[str, Any], force: bool = False, dry_run: bool = False) -> Tuple[bool, int, int]:
    """E1: Mirror not OK (persistent)."""
    mirror_state = str(status.get("mirror_state", "UNKNOWN"))
    mirror_reason = str(status.get("mirror_reason", ""))
    mirror_rc = status.get("mirror_rsync_exit_code", None)
    
    # If forcing (test mode), use test state if current is OK
    if force and mirror_state == "OK":
        mirror_state = "ERROR"
        mirror_reason = "Test alert - forced trigger"
        mirror_rc = 1
    elif not force and mirror_state == "OK":
        return False, 0, 0
    
    check_count = 1
    key = _key("E1_mirror_not_ok")
    sig = f"mirror:{mirror_state}|rc:{mirror_rc}|reason:{mirror_reason}"
    
    # When forcing, always send (bypass all checks)
    should_send = force or _should_send_persistent(notify_state, key, sig, force=force)
    
    if should_send:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"""Mirror/Sync Alert: Mirror Status Not OK

The mirror/sync operation is not in a healthy state.

Mirror State: {mirror_state}
Rsync Exit Code: {mirror_rc if mirror_rc is not None else "N/A"}
Reason: {mirror_reason if mirror_reason else "No reason provided"}

Timestamp: {timestamp}

Action Required:
- Check mirror/sync logs for details
- Verify source and destination paths are accessible
- Check network connectivity if using remote destination
- Review rsync configuration
- Verify sufficient disk space at destination

This alert will repeat every {PERSISTENT_REPEAT_SEC // 3600} hours while the condition persists.
"""
        res = _send("Mirror/Sync Alert: Status Not OK", body, dry_run=dry_run)
        if res.get("ok"):
            _mark_sent(notify_state, key, sig)
            return True, 1, 0
        elif force:
            # In test mode, return success even if email failed (for testing purposes)
            _mark_sent(notify_state, key, sig)
            print(f"NOTE: Email send failed but test mode forced: {res.get('error', res.get('reason', 'unknown'))}", file=sys.stderr)
            return True, 1, 0
    
    return False, 0, 1

def _check_g1_control_service_down(notify_state: Dict[str, Any], force: bool = False, dry_run: bool = False) -> Tuple[bool, int, int]:
    """G1: Control service down (persistent)."""
    active = _systemd_is_active(CONTROL_SERVICE)
    
    # If forcing (test mode), bypass condition check
    if not force and active is not False:
        return False, 0, 0
    
    check_count = 1
    key = _key("G1_control_service_down")
    sig = "down"
    
    # When forcing, always send (bypass all checks)
    should_send = force or _should_send_persistent(notify_state, key, sig, force=force)
    
    if should_send:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"""Service Health Alert: Control API Service Down

The control API service is not active.

Service: {CONTROL_SERVICE}
Status: Not active

Timestamp: {timestamp}

Action Required:
- Check service status: systemctl status {CONTROL_SERVICE}
- Review service logs: journalctl -u {CONTROL_SERVICE}
- Restart service if needed: systemctl restart {CONTROL_SERVICE}
- Verify service configuration

This alert will repeat every {PERSISTENT_REPEAT_SEC // 3600} hours while the service remains down.
"""
        res = _send("Service Alert: Control API Down", body, dry_run=dry_run)
        if res.get("ok"):
            _mark_sent(notify_state, key, sig)
            return True, 1, 0
        elif force:
            # In test mode, return success even if email failed (for testing purposes)
            _mark_sent(notify_state, key, sig)
            print(f"NOTE: Email send failed but test mode forced: {res.get('error', res.get('reason', 'unknown'))}", file=sys.stderr)
            return True, 1, 0
    
    return False, 0, 1

def _check_g2_backup_timer_issue(notify_state: Dict[str, Any], force: bool = False, dry_run: bool = False) -> Tuple[bool, int, int]:
    """G2: Backup timer issue (persistent)."""
    t_enabled = _systemd_is_enabled(BACKUP_TIMER)
    t_active = _systemd_is_active(BACKUP_TIMER)
    
    # If forcing (test mode), bypass condition check
    if not force and (t_enabled is not False) and (t_active is not False):
        return False, 0, 0
    
    check_count = 1
    key = _key("G2_backup_timer_problem")
    sig = f"enabled:{t_enabled}|active:{t_active}"
    
    # When forcing, always send (bypass all checks)
    should_send = force or _should_send_persistent(notify_state, key, sig, force=force)
    
    if should_send:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"""Service Health Alert: Backup Timer Issue

The backup timer has a configuration or status problem.

Timer: {BACKUP_TIMER}
Enabled: {t_enabled if t_enabled is not None else "Unknown"}
Active: {t_active if t_active is not None else "Unknown"}

Timestamp: {timestamp}

Action Required:
- Check timer status: systemctl status {BACKUP_TIMER}
- Check if enabled: systemctl is-enabled {BACKUP_TIMER}
- Enable timer if needed: systemctl enable {BACKUP_TIMER}
- Start timer if needed: systemctl start {BACKUP_TIMER}
- Review timer configuration

This alert will repeat every {PERSISTENT_REPEAT_SEC // 3600} hours while the issue persists.
"""
        res = _send("Service Alert: Backup Timer Issue", body, dry_run=dry_run)
        if res.get("ok"):
            _mark_sent(notify_state, key, sig)
            return True, 1, 0
        elif force:
            # In test mode, return success even if email failed (for testing purposes)
            _mark_sent(notify_state, key, sig)
            print(f"NOTE: Email send failed but test mode forced: {res.get('error', res.get('reason', 'unknown'))}", file=sys.stderr)
            return True, 1, 0
    
    return False, 0, 1

# =========================
# Manual test trigger functions
# =========================
def _trigger_test_alert(alert_id: str, notify_state: Dict[str, Any], status: Dict[str, Any], dry_run: bool = False) -> bool:
    """Manually trigger a specific alert for testing."""
    # Functions that need status parameter
    alerts_with_status = {
        "A1": lambda ns, s, f, dr: _check_a1_backup_not_ok(ns, s, f, dr),
        "A2": lambda ns, s, f, dr: _check_a2_backup_recovered(ns, s, f, dr),
        "E1": lambda ns, s, f, dr: _check_e1_mirror_not_ok(ns, s, f, dr),
        "E4": lambda ns, s, f, dr: _check_e4_mirror_recovered(ns, s, f, dr),
    }
    
    # Functions that don't need status parameter
    alerts_without_status = {
        "C3_primary": lambda ns, s, f, dr: _check_c3_low_space(ns, "Primary", PRIMARY_MOUNT, f, dr),
        "C3_backup": lambda ns, s, f, dr: _check_c3_low_space(ns, "Backup", BACKUP_MOUNT, f, dr),
        "G1": lambda ns, s, f, dr: _check_g1_control_service_down(ns, f, dr),
        "G2": lambda ns, s, f, dr: _check_g2_backup_timer_issue(ns, f, dr),
    }
    
    # Combine both maps
    alert_map = {**alerts_with_status, **alerts_without_status}
    
    if alert_id not in alert_map:
        print(f"ERROR: Unknown alert ID: {alert_id}", file=sys.stderr)
        print(f"Available alerts: {', '.join(ALL_ALERTS)}", file=sys.stderr)
        return False
    
    print(f"Testing alert: {alert_id}", file=sys.stderr)
    sent, _, _ = alert_map[alert_id](notify_state, status, True, dry_run)
    
    if sent:
        print(f"SUCCESS: Alert {alert_id} {'would be sent' if dry_run else 'sent'}", file=sys.stderr)
    else:
        print(f"WARNING: Alert {alert_id} check did not trigger (condition may not be met)", file=sys.stderr)
    
    return sent

# =========================
# Main notification logic
# =========================
def main() -> int:
    parser = argparse.ArgumentParser(description="Chronovault notification system")
    parser.add_argument("--test", metavar="ALERT_ID", help="Force trigger a specific alert (e.g., A1, C1)")
    parser.add_argument("--test-all", action="store_true", help="Force trigger all alerts once")
    parser.add_argument("--simulate", metavar="CONDITION", help="Simulate conditions in-memory (simulate-error, simulate-frozen, simulate-mirror-error, simulate-growth, simulate-high-change, simulate-warnings)")
    parser.add_argument("--status-file", metavar="PATH", help="Use alternative status.json file for testing")
    parser.add_argument("--dry-run", action="store_true", help="Show what would fire without sending emails")
    parser.add_argument("--commit-state", action="store_true", help="Write state file even in test mode (default: test mode doesn't write state)")
    parser.add_argument("--force", action="store_true", help="Bypass initial run guard and suppression")
    parser.add_argument("--list-alerts", action="store_true", help="List all available alert IDs")
    
    args = parser.parse_args()
    
    if args.list_alerts:
        print("Available alert IDs:")
        for alert in ALL_ALERTS:
            print(f"  {alert}")
        return 0
    
    # Determine status file path
    status_path = args.status_file or STATUS_PATH
    
    notify_state: Dict[str, Any] = _load_json(NOTIFY_STATE_PATH) or {}
    status: Dict[str, Any] = _load_json(status_path) or {}
    
    # Check for initial run (no state file exists)
    is_initial_run = not os.path.exists(NOTIFY_STATE_PATH) or len(notify_state) == 0
    
    # Apply simulation if requested (in-memory only, does not modify files)
    if args.simulate:
        status = _simulate_status(status, args.simulate)
        print(f"Simulation mode: {args.simulate} applied (in-memory only)", file=sys.stderr)
    
    # Initial run guard: don't send transition alerts on first run unless --force
    if is_initial_run and not args.force and not args.test and not args.test_all:
        print("Initial run detected. Recording current state. Use --force to send alerts on first run.", file=sys.stderr)
        # Record current state for transition detection
        state = str(status.get("state", "UNKNOWN"))
        mirror_state = str(status.get("mirror_state", "UNKNOWN"))
        _mark_seen(notify_state, _key("A2_backup_ok"), state)
        _mark_seen(notify_state, _key("E4_mirror_ok"), mirror_state)
        # Save state and exit
        _write_json_atomic(NOTIFY_STATE_PATH, notify_state)
        return 0
    
    # Determine if we should write state (default: yes, unless in test mode without --commit-state)
    write_state = True
    if (args.test or args.test_all or args.simulate) and not args.commit_state:
        write_state = False
        if args.dry_run:
            print("[DRY-RUN] State file would not be written (test mode)", file=sys.stderr)
        else:
            print("Test mode: state file will not be written (use --commit-state to persist)", file=sys.stderr)
    
    # Handle manual test triggers
    if args.test:
        _trigger_test_alert(args.test, notify_state, status, dry_run=args.dry_run)
        if write_state:
            _write_json_atomic(NOTIFY_STATE_PATH, notify_state)
        return 0
    
    if args.test_all:
        print("Testing all alerts...", file=sys.stderr)
        for alert_id in ALL_ALERTS:
            _trigger_test_alert(alert_id, notify_state, status, dry_run=args.dry_run)
        if write_state:
            _write_json_atomic(NOTIFY_STATE_PATH, notify_state)
        return 0
    
    # Normal operation: run all checks (only 7 alerts)
    sent_count = 0
    suppressed_count = 0
    check_count = 0
    
    # Group A: Backup status
    sent, checks, suppressed = _check_a1_backup_not_ok(notify_state, status, force=args.force, dry_run=args.dry_run)
    sent_count += sent
    check_count += checks
    suppressed_count += suppressed
    
    sent, checks, suppressed = _check_a2_backup_recovered(notify_state, status, force=args.force, dry_run=args.dry_run)
    sent_count += sent
    check_count += checks
    suppressed_count += suppressed
    
    # Group C: Low space (Primary + Backup)
    sent, checks, suppressed = _check_c3_low_space(notify_state, "Primary", PRIMARY_MOUNT, force=args.force, dry_run=args.dry_run)
    sent_count += sent
    check_count += checks
    suppressed_count += suppressed
    
    sent, checks, suppressed = _check_c3_low_space(notify_state, "Backup", BACKUP_MOUNT, force=args.force, dry_run=args.dry_run)
    sent_count += sent
    check_count += checks
    suppressed_count += suppressed
    
    # Group E: Mirror/sync
    sent, checks, suppressed = _check_e1_mirror_not_ok(notify_state, status, force=args.force, dry_run=args.dry_run)
    sent_count += sent
    check_count += checks
    suppressed_count += suppressed
    
    sent, checks, suppressed = _check_e4_mirror_recovered(notify_state, status, force=args.force, dry_run=args.dry_run)
    sent_count += sent
    check_count += checks
    suppressed_count += suppressed
    
    # Group G: System health
    sent, checks, suppressed = _check_g1_control_service_down(notify_state, force=args.force, dry_run=args.dry_run)
    sent_count += sent
    check_count += checks
    suppressed_count += suppressed
    
    sent, checks, suppressed = _check_g2_backup_timer_issue(notify_state, force=args.force, dry_run=args.dry_run)
    sent_count += sent
    check_count += checks
    suppressed_count += suppressed
    
    # Save state if needed
    if write_state:
        _write_json_atomic(NOTIFY_STATE_PATH, notify_state)
    
    mode_str = "[DRY-RUN] " if args.dry_run else ""
    print(
        f"{mode_str}[chronovault-notify] run complete: sent={sent_count} suppressed={suppressed_count} checks={check_count}",
        flush=True,
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
