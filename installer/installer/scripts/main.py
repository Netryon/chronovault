import os
import json
import subprocess
from typing import List
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

#from mailer import send_email

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Constants
TOKEN = os.environ.get("CHRONOVAULT_UI_TOKEN")
UI_DIR = os.environ.get("CHRONOVAULT_UI_DIR", "/opt/chronovault/ui")

STATUS_PATH = "/var/lib/chronovault/status.json"
RESTORE_POINTS_PATH = "/var/lib/chronovault/restore_points.json"

SCRIPTS_DIR = "/opt/chronovault/scripts"
BACKUP_RUN = f"{SCRIPTS_DIR}/chronovault-backup-run"
RESTORE_RUN = f"{SCRIPTS_DIR}/chronovault-restore"

SNAPSHOT_BASE = "/mnt/backup/chronovault/snapshots"
APPROVE_FILE = "/var/lib/chronovault/state/approve_once"

# Helper functions
def require_token(request: Request) -> None:
    t = request.query_params.get("t")
    if not TOKEN or not t or t != TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")


def load_json(path: str):
    """Load JSON file, return empty dict if file doesn't exist or can't be read."""
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return {}
    except json.JSONDecodeError:
        return {}
    except Exception:
        return {}


def sudo_run(args: List[str]):
    proc = subprocess.run(
        ["sudo", "-n"] + args,
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "cmd": ["sudo", "-n"] + args,
                "rc": proc.returncode,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
            },
        )

    return {
        "ok": True,
        "rc": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }

# API endpoints (token required)
@app.get("/health")
def health(request: Request):
    require_token(request)
    return {"status": "ok"}


@app.get("/status")
def status(request: Request):
    require_token(request)
    status_data = load_json(STATUS_PATH)
    
    # Check for stale lock file
    lock_file = Path("/var/lib/chronovault/state/backup.lock")
    if lock_file.exists():
        import time
        import subprocess
        
        lock_age = time.time() - lock_file.stat().st_mtime
        is_stale = False
        
        if lock_age > 7200:
            is_stale = True
        else:
            try:
                lock_pid = int(lock_file.read_text().strip())
                result = subprocess.run(
                    ["ps", "-p", str(lock_pid)],
                    capture_output=True,
                    timeout=5
                )
                if result.returncode != 0:
                    is_stale = True
            except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
                if lock_age > 600:
                    is_stale = True
        
        if is_stale:
            try:
                lock_file.unlink()
            except Exception:
                pass
            if status_data.get('state') == 'RUNNING':
                status_data['state'] = 'ERROR'
                status_data['reason'] = 'Stale backup lock detected and removed'
    
    return status_data


@app.get("/restore-points")
def restore_points(request: Request):
    require_token(request)
    return load_json(RESTORE_POINTS_PATH)


@app.post("/action/run-backup-now")
def run_backup_now(request: Request):
    require_token(request)
    
    # Check if backup is already running
    lock_file = Path("/var/lib/chronovault/state/backup.lock")
    if lock_file.exists():
        import time
        import subprocess
        
        lock_age = time.time() - lock_file.stat().st_mtime
        is_stale = False
        
        if lock_age > 7200:
            is_stale = True
        else:
            try:
                lock_pid = int(lock_file.read_text().strip())
                result = subprocess.run(
                    ["ps", "-p", str(lock_pid)],
                    capture_output=True,
                    timeout=5
                )
                if result.returncode != 0:
                    is_stale = True
            except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
                if lock_age > 600:
                    is_stale = True
        
        if is_stale:
            try:
                lock_file.unlink()
            except Exception:
                pass
        else:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Backup already in progress",
                    "lock_file": str(lock_file),
                    "message": "A backup operation is currently running. Please wait for it to complete."
                }
            )
    
    return sudo_run([BACKUP_RUN])


@app.post("/action/approve-once")
def approve_once(request: Request):
    require_token(request)
    return sudo_run(["/usr/bin/touch", APPROVE_FILE])


class RestoreRequest(BaseModel):
    type: str   # "daily" or "weekly"
    date: str   # "YYYY-MM-DD"
    apps: str   # "both", "immich", "nextcloud"


@app.post("/action/restore")
def restore_now(request: Request, body: RestoreRequest):
    require_token(request)

    if body.type not in ("daily", "weekly"):
        raise HTTPException(status_code=400, detail="type must be daily or weekly")

    if body.apps not in ("both", "immich", "nextcloud"):
        raise HTTPException(status_code=400, detail="apps must be both, immich, or nextcloud")

    snapshot_path = f"{SNAPSHOT_BASE}/{body.type}/{body.date}"
    cmd = [RESTORE_RUN, snapshot_path]

    if body.apps != "both":
        cmd += ["--apps", body.apps]

    return sudo_run(cmd)


@app.post("/action/test-email")
def test_email(request: Request):
    require_token(request)

    result = send_email(
        subject="Chronovault Test Email",
        body="This is a test email from Chronovault.\n\nIf you received this, SMTP is working."
    )

    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result)

    return {"ok": True}

# UI serving (no token required, must be last)
if os.path.isdir(UI_DIR):
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    @app.get("/", include_in_schema=False)
    def ui_root():
        return FileResponse(os.path.join(UI_DIR, "index.html"))

    @app.get("/{path:path}", include_in_schema=False)
    def ui_fallback(path: str):
        candidate = os.path.join(UI_DIR, path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(UI_DIR, "index.html"))