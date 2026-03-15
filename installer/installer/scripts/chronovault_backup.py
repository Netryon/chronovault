#!/usr/bin/env python3
"""
chronovault Backup - Python Logic
Step 6: Status reporting + BACKUP identity verification + source size calculation + baseline mirror sync + abnormality detection
"""

import sys
import json
import os
import subprocess
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# EST timezone (handles EST/EDT automatically)
EST_TZ = ZoneInfo("America/New_York")

STATUS_FILE = Path("/var/lib/chronovault/status.json")
PRIMARY_MOUNT = Path("/mnt/primary")
BACKUP_MOUNT = Path("/mnt/backup")
BACKUP_IDENTITY_FILE = BACKUP_MOUNT / "chronovault" / "metadata" / "IDENTITY"
BACKUP_CURRENT = BACKUP_MOUNT / "chronovault" / "current"
FREEZE_FLAG_FILE = Path("/var/lib/chronovault/state/frozen")
APPROVE_ONCE_FILE = Path("/var/lib/chronovault/state/approve_once")
SNAPSHOTS_DAILY_DIR = BACKUP_MOUNT / "chronovault" / "snapshots" / "daily"
SNAPSHOTS_WEEKLY_DIR = BACKUP_MOUNT / "chronovault" / "snapshots" / "weekly"

# Authoritative paths for abnormality detection
# Immich: only the upload/upload directory contains user photos (originals)
IMMICH_DATA_PATH = PRIMARY_MOUNT / "apps" / "immich" / "upload" / "upload"
IMMICH_BACKUP_PATH = BACKUP_CURRENT / "apps" / "immich" / "upload" / "upload"
NEXTCLOUD_DATA_PATH = PRIMARY_MOUNT / "apps" / "nextcloud" / "data"
NEXTCLOUD_BACKUP_PATH = BACKUP_CURRENT / "apps" / "nextcloud" / "data"


def read_status():
    """Read existing status.json if it exists, return dict or empty dict"""
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            # If file is corrupted, start fresh but log warning
            print(f"Warning: Could not read existing status.json: {e}")
            return {}
    return {}


def write_status(status_data, max_retries=3):
    """
    Atomically write status.json by writing to temp file then renaming.
    Retries on failure to handle race conditions and temporary I/O errors.
    """
    # Ensure directory exists
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Write to temp file first
    temp_file = STATUS_FILE.with_suffix('.tmp')
    
    last_error = None
    for attempt in range(max_retries):
        try:
            # Clean up any existing temp file from previous failed attempt
            if temp_file.exists():
                temp_file.unlink()
            
            with open(temp_file, 'w') as f:
                json.dump(status_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())  # Ensure data is written to disk
            
            # Atomic rename
            temp_file.replace(STATUS_FILE)
            return  # Success
            
        except (OSError, IOError, PermissionError) as e:
            last_error = e
            if attempt < max_retries - 1:
                # Wait a short time before retry (exponential backoff)
                import time
                time.sleep(0.1 * (2 ** attempt))
                continue
            else:
                # Final attempt failed - clean up and raise
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
                raise RuntimeError(f"Failed to write status.json after {max_retries} attempts: {e}") from e
        except Exception as e:
            # Non-retryable error (e.g., JSON encoding error)
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
            raise


def update_status_start():
    """Update status at backup start"""
    status = read_status()
    
    # Check for stale RUNNING state (older than 2 hours) and clean it up
    if status.get('state') == 'RUNNING' and status.get('last_attempt_time'):
        try:
            last_attempt = datetime.fromisoformat(status['last_attempt_time'].replace('Z', '+00:00'))
            if last_attempt.tzinfo is None:
                # If no timezone info, assume EST
                last_attempt = last_attempt.replace(tzinfo=EST_TZ)
            now = datetime.now(EST_TZ)
            age_hours = (now - last_attempt).total_seconds() / 3600
            
            if age_hours > 2:
                print(f"WARNING: Detected stale RUNNING state (age: {age_hours:.1f} hours), cleaning up...")
                # Clear stale RUNNING state - will be set to new RUNNING below
                status['state'] = 'ERROR'
                status['reason'] = f'Stale RUNNING state detected and cleared (was running for {age_hours:.1f} hours)'
        except (ValueError, TypeError, KeyError) as e:
            print(f"WARNING: Could not check for stale RUNNING state: {e}")
    
    # Preserve last_success_time if it exists
    status['last_attempt_time'] = datetime.now(EST_TZ).isoformat()
    status['state'] = 'RUNNING'
    status['reason'] = 'Backup started'
    
    try:
        write_status(status)
        print(f"Status updated: backup started at {status['last_attempt_time']}")
    except Exception as e:
        print(f"ERROR: Failed to update status to RUNNING: {e}", file=sys.stderr)
        # This is critical - if we can't set RUNNING state, backup might appear stuck
        # But we'll continue anyway since the backup process itself is more important


def update_status_success():
    """Update status on successful completion"""
    try:
        status = read_status()
        
        status['last_success_time'] = datetime.now(EST_TZ).isoformat()
        status['state'] = 'OK'
        status['reason'] = 'Backup completed'
        
        write_status(status)
        print(f"Status updated: backup completed successfully at {status['last_success_time']}")
    except Exception as e:
        print(f"ERROR: Failed to update status to success: {e}", file=sys.stderr)
        # Don't raise - backup succeeded even if status update failed


def update_status_error(reason):
    """Update status on error"""
    try:
        status = read_status()
        
        status['state'] = 'ERROR'
        status['reason'] = reason
        
        write_status(status)
        print(f"Status updated: ERROR - {reason}")
    except Exception as e:
        print(f"ERROR: Failed to update status to error: {e}", file=sys.stderr)
        # Still raise the original error - this is a critical failure
        print(f"Original error reason: {reason}", file=sys.stderr)


def verify_backup_identity():
    """
    Step 3: Verify BACKUP identity file exists.
    Ensures directories exist (non-destructive).
    Returns True if identity exists, False otherwise.
    """
    # Ensure directories exist (non-destructive)
    BACKUP_IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Check if identity file exists
    if not BACKUP_IDENTITY_FILE.exists():
        return False
    
    return True


def calculate_source_total_bytes():
    """
    Step 4: Calculate total bytes of PRIMARY dataset.
    Uses 'du -sb /mnt/primary' to get total size in bytes.
    Returns total bytes as integer.
    """
    try:
        result = subprocess.run(
            ['du', '-sb', str(PRIMARY_MOUNT)],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse output: "1234567890\t/mnt/primary"
        total_bytes = int(result.stdout.split()[0])
        return total_bytes
        
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to calculate source total bytes: {e.stderr}") from e
    except (ValueError, IndexError) as e:
        raise RuntimeError(f"Failed to parse du output: {e}") from e


def verify_backup_mounted():
    """
    Step 5.1: Verify BACKUP is mounted.
    Returns True if BACKUP is mounted, False otherwise.
    """
    return BACKUP_MOUNT.is_mount()


def perform_mirror_sync():
    """
    Step 5: Perform REAL baseline mirror sync using rsync (NOT a dry run).
    Creates a rolling mirror at /mnt/backup/chronovault/current/
    This is the actual backup operation that writes to BACKUP disk.
    
    Returns tuple: (success: bool, rsync_exit_code: int, error_message: str)
    """
    # Step 5.2: Ensure destination exists
    BACKUP_CURRENT.mkdir(parents=True, exist_ok=True)
    
    # Step 5.3: Build rsync command for REAL sync (NO -n flag = actual backup)
    # -a: archive mode (preserves permissions, timestamps, etc.)
    # -v: verbose (show what's being transferred)
    # --delete: remove files in destination that don't exist in source
    # --exclude: exclude Postgres data directories (we use dumps instead)
    # Trailing slashes ensure contents are mirrored correctly
    source_path = f"{PRIMARY_MOUNT}/"
    dest_path = f"{BACKUP_CURRENT}/"
    
    rsync_cmd = [
        'rsync',
        '-aHAX',  # archive + hardlinks + ACLs + extended attributes
        '--numeric-ids',  # Preserve numeric UIDs/GIDs exactly (perfect restore)
        '--delete',
        '--info=stats2,progress2',  # Better progress output
        '--exclude=apps/immich/postgres/',  # Exclude live PGDATA (use dumps instead)
        '--exclude=apps/postgres/nextcloud/',  # Exclude live PGDATA (use dumps instead)
        source_path,
        dest_path
    ]
    
    try:
        print(f"Running REAL rsync mirror sync (this will write to BACKUP):")
        print(f"  Source: {source_path}")
        print(f"  Destination: {dest_path}")
        print("This may take a long time on first run...")
        
        # Run rsync with real-time output (not captured) so user can see progress
        result = subprocess.run(
            rsync_cmd,
            check=False  # Don't raise on non-zero exit, we'll handle it
        )
        
        if result.returncode == 0:
            print("rsync mirror sync completed successfully")
            return True, result.returncode, None
        else:
            error_msg = f"rsync failed with exit code {result.returncode}"
            return False, result.returncode, error_msg
            
    except Exception as e:
        return False, -1, f"rsync execution failed: {e}"


def update_mirror_status(success, rsync_exit_code, error_message):
    """
    Step 5.4: Update status.json with mirror-specific fields.
    """
    try:
        status = read_status()
        
        now_iso = datetime.now(EST_TZ).isoformat()
        
        status['mirror_last_attempt_time'] = now_iso
        status['mirror_rsync_exit_code'] = rsync_exit_code
        status['mirror_dest_path'] = str(BACKUP_CURRENT)
        status['mirror_source_path'] = str(PRIMARY_MOUNT)
        
        if success:
            status['mirror_state'] = 'OK'
            status['mirror_reason'] = 'Mirror sync completed successfully'
            status['mirror_last_success_time'] = now_iso
        else:
            status['mirror_state'] = 'ERROR'
            status['mirror_reason'] = error_message or f"rsync failed with exit code {rsync_exit_code}"
            # Preserve last_success_time if it exists
            if 'mirror_last_success_time' not in status:
                status['mirror_last_success_time'] = None
        
        write_status(status)
        
        if success:
            print(f"Mirror status updated: OK at {now_iso}")
        else:
            print(f"Mirror status updated: ERROR - {status['mirror_reason']}")
    except Exception as e:
        print(f"WARNING: Failed to update mirror status: {e}", file=sys.stderr)
        # Don't raise - mirror status is informational


def should_exclude_path(path_str, excludes=None):
    """
    Check if a path should be excluded based on exclude patterns.
    
    Args:
        path_str: Path string (can be relative or absolute)
        excludes: List of exclude patterns (e.g., ['**/pg_wal/**', 'apps/postgres/'])
    
    Returns:
        True if path should be excluded, False otherwise
    """
    if not excludes:
        return False
    
    # Convert path to string and normalize separators
    path_normalized = str(path_str).replace('\\', '/')
    
    for exclude_pattern in excludes:
        # Convert pattern to regex-like matching
        # **/pg_wal/** matches any path containing /pg_wal/
        # apps/postgres/ matches paths starting with apps/postgres/
        
        # Handle **/pattern/** (matches pattern anywhere)
        if exclude_pattern.startswith('**/') and exclude_pattern.endswith('/**'):
            pattern = exclude_pattern[3:-3]  # Remove **/ and /**
            if f'/{pattern}/' in path_normalized or path_normalized.endswith(f'/{pattern}'):
                return True
        # Handle **/pattern (matches pattern at end)
        elif exclude_pattern.startswith('**/'):
            pattern = exclude_pattern[3:]  # Remove **/
            if path_normalized.endswith(f'/{pattern}') or path_normalized == pattern:
                return True
        # Handle pattern/** (matches pattern at start)
        elif exclude_pattern.endswith('/**'):
            pattern = exclude_pattern[:-3]  # Remove /**
            if path_normalized.startswith(f'{pattern}/') or path_normalized == pattern:
                return True
        # Handle exact match or prefix match
        else:
            if path_normalized.startswith(exclude_pattern) or path_normalized == exclude_pattern:
                return True
    
    return False


def calculate_rsync_changes(source_path, dest_path, excludes=None):
    """
    Calculate changed and deleted bytes using rsync dry-run with itemize-changes.
    
    Returns tuple: (changed_bytes, deleted_bytes, deleted_files_count)
    """
    rsync_cmd = [
        'rsync',
        '-aHAX',
        '--numeric-ids',
        '-n',  # dry-run
        '--delete',
        '--itemize-changes',
    ]
    
    # Add excludes if provided
    if excludes:
        for exclude in excludes:
            rsync_cmd.extend(['--exclude', exclude])
    
    rsync_cmd.extend([f"{source_path}/", f"{dest_path}/"])
    
    try:
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            check=False  # Don't raise on non-zero - rsync may exit non-zero for various reasons
        )
        
        # rsync exit codes: 0 = success, 1-23 = various conditions (some are OK for dry-run)
        # Exit code 24 = partial transfer due to vanished source files (OK for dry-run)
        if result.returncode not in [0, 23, 24]:
            # Only treat as error if it's not a known "acceptable" exit code
            raise RuntimeError(f"rsync dry-run failed with exit code {result.returncode}: {result.stderr[:500]}")
        
        changed_bytes = 0
        deleted_bytes = 0
        deleted_files_count = 0
        
        output_lines = result.stdout.splitlines()
        stderr_lines = result.stderr.splitlines() if result.stderr else []
        
        if output_lines or stderr_lines:
            print(f"  DEBUG: rsync stdout has {len(output_lines)} lines, stderr has {len(stderr_lines)} lines", flush=True)
            debug_count = 0
            for line in output_lines:
                if line.strip() and debug_count < 10:
                    print(f"  DEBUG: rsync stdout: {line[:100]}", flush=True)
                    debug_count += 1
            if stderr_lines:
                debug_count = 0
                for line in stderr_lines:
                    if line.strip() and debug_count < 10:
                        print(f"  DEBUG: rsync stderr: {line[:100]}", flush=True)
                        debug_count += 1
        
        all_lines = output_lines + stderr_lines
        
        for line in all_lines:
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('*deleting'):
                path_part = line[9:].strip()
                
                # Skip if path matches exclude patterns
                if should_exclude_path(path_part, excludes):
                    continue
                
                deleted_files_count += 1
                if path_part:
                    dest_file = Path(dest_path) / path_part.lstrip('/')
                    if dest_file.exists() and dest_file.is_file():
                        try:
                            file_size = dest_file.stat().st_size
                            deleted_bytes += file_size
                            if deleted_files_count <= 5:
                                print(f"    DEBUG: Found deletion: {path_part} ({file_size:,} bytes)", flush=True)
                        except (OSError, ValueError) as e:
                            if deleted_files_count <= 5:
                                print(f"    DEBUG: Could not get size for {path_part}: {e}", flush=True)
                            pass
                    elif deleted_files_count <= 5:
                        print(f"    DEBUG: Deletion path not found: {dest_file}", flush=True)
            
            elif line.startswith('>f'):
                parts = line.split()
                if len(parts) >= 2:
                    rel_path = parts[-1].lstrip('/')
                    
                    if should_exclude_path(rel_path, excludes):
                        continue
                    
                    try:
                        size = int(parts[1])
                        changed_bytes += size
                    except ValueError:
                        if len(parts) >= 2:
                            source_file = Path(source_path) / rel_path
                            if source_file.exists() and source_file.is_file():
                                try:
                                    changed_bytes += source_file.stat().st_size
                                except (OSError, ValueError):
                                    pass
        
        # Fallback: manual comparison if rsync didn't report deletions
        if deleted_files_count == 0 and Path(dest_path).exists():
            print(f"  DEBUG: No deletions from rsync, doing manual comparison...", flush=True)
            try:
                dest_files = set()
                dest_file_sizes = {}
                for file_path in Path(dest_path).rglob('*'):
                    if file_path.is_file():
                        try:
                            rel_path = file_path.relative_to(Path(dest_path))
                            if should_exclude_path(str(rel_path), excludes):
                                continue
                            dest_files.add(rel_path)
                            dest_file_sizes[rel_path] = file_path.stat().st_size
                        except (ValueError, OSError):
                            pass
                
                source_files = set()
                for file_path in Path(source_path).rglob('*'):
                    if file_path.is_file():
                        try:
                            rel_path = file_path.relative_to(Path(source_path))
                            if should_exclude_path(str(rel_path), excludes):
                                continue
                            source_files.add(rel_path)
                        except (ValueError, OSError):
                            pass
                
                files_to_delete = dest_files - source_files
                if files_to_delete:
                    print(f"  DEBUG: Manual comparison found {len(files_to_delete)} files that would be deleted", flush=True)
                    deleted_files_count = len(files_to_delete)
                    for rel_path in files_to_delete:
                        if rel_path in dest_file_sizes:
                            deleted_bytes += dest_file_sizes[rel_path]
                            if deleted_files_count <= 5:
                                print(f"    DEBUG: Manual deletion: {rel_path} ({dest_file_sizes[rel_path]:,} bytes)", flush=True)
            except Exception as e:
                print(f"  DEBUG: Manual comparison failed: {e}", flush=True)
        
        print(f"  DEBUG: Final results - changed: {changed_bytes:,} bytes, deleted: {deleted_bytes:,} bytes, deleted_files: {deleted_files_count}", flush=True)
        return changed_bytes, deleted_bytes, deleted_files_count
        
    except Exception as e:
        raise RuntimeError(f"Failed to calculate rsync changes: {e}") from e


def detect_abnormalities():
    """
    Detect abnormal behavior before mirror sync by comparing PRIMARY to current BACKUP state.
    
    Returns tuple: (frozen: bool, warnings: list, metrics: dict)
    """
    print("Detecting abnormalities...", flush=True)
    
    warnings = []
    frozen = False
    metrics = {}
    
    # Check if BACKUP mirror exists (if not, this is first run - skip detection)
    if not BACKUP_CURRENT.exists():
        print("  BACKUP mirror does not exist yet (first run) - skipping abnormality detection", flush=True)
        return frozen, warnings, metrics
    
    # Global catastrophic change detector
    print("Checking global catastrophic changes...", flush=True)
    try:
        print(f"  Comparing: {PRIMARY_MOUNT} -> {BACKUP_CURRENT}", flush=True)
        # Exclude PostgreSQL high-churn directories to avoid false ransomware freezes
        global_excludes = [
            'apps/immich/postgres/',      # Exclude live PGDATA (we use dumps instead)
            'apps/postgres/nextcloud/',   # Exclude live PGDATA (we use dumps instead)
            '**/pg_wal/**',               # Required: WAL files rotate constantly, large size
            '**/pg_replslot/**',          # Recommended: replication slots (high churn)
            '**/pg_stat_tmp/**',          # Recommended: temporary stats (high churn)
            '**/pg_logical/snapshots/**', # Recommended: logical replication snapshots (high churn)
        ]
        global_changed, global_deleted, _ = calculate_rsync_changes(
            str(PRIMARY_MOUNT),
            str(BACKUP_CURRENT),
            excludes=global_excludes
        )
        
        # Get total bytes from status (already calculated in Step 4)
        # T = global_total_bytes (size of PRIMARY)
        status = read_status()
        global_total = status.get('source_total_bytes', 0)
        
        # Catastrophic% = ((C + D) × 100) / T
        # C = changed_bytes, D = deleted_bytes, T = global_total_bytes
        if global_total > 0:
            global_change_pct = ((global_changed + global_deleted) * 100) / global_total
            metrics['global_change_pct'] = round(global_change_pct, 2)
            
            print(f"  Global changes: {global_changed:,} bytes changed, {global_deleted:,} bytes deleted")
            print(f"  Global catastrophic percentage: {global_change_pct:.2f}%")
            
            if global_change_pct >= 80:
                frozen = True
                print(f"  FROZEN: Catastrophic change detected ({global_change_pct:.2f}% >= 80%)")
                # Create freeze flag file
                FREEZE_FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
                FREEZE_FLAG_FILE.touch()
                print(f"  Freeze flag created: {FREEZE_FLAG_FILE}")
        else:
            # If T == 0, set to 0
            global_change_pct = 0
            metrics['global_change_pct'] = 0
            print("  WARNING: Could not get global_total_bytes from status (T=0, setting catastrophic% to 0)")
            
    except Exception as e:
        error_msg = f"Global change detection failed: {e}"
        print(f"  WARNING: {error_msg}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        warnings.append(error_msg)
    
    # Immich deletion detector
    print("Checking Immich deletions...")
    try:
        if IMMICH_DATA_PATH.exists() and IMMICH_BACKUP_PATH.exists():
            print(f"  Comparing: {IMMICH_DATA_PATH} -> {IMMICH_BACKUP_PATH}")
            immich_changed, immich_deleted, immich_deleted_files = calculate_rsync_changes(
                str(IMMICH_DATA_PATH),
                str(IMMICH_BACKUP_PATH)
            )
            
            # Calculate total bytes for Immich BACKUP (B = backup_before_bytes)
            # This is the size of BACKUP mirror before rsync runs
            result = subprocess.run(
                ['du', '-sb', str(IMMICH_BACKUP_PATH)],
                capture_output=True,
                text=True,
                check=True
            )
            immich_backup_total = int(result.stdout.split()[0])
            
            # Deleted% = (D × 100) / B
            # D = deleted_bytes (from rsync dry-run), B = backup_before_bytes
            if immich_backup_total > 0:
                immich_delete_pct = (immich_deleted * 100) / immich_backup_total
                metrics['immich_delete_pct'] = round(immich_delete_pct, 2)
                
                print(f"  Immich BACKUP (B): {immich_backup_total:,} bytes")
                print(f"  Immich deleted (D): {immich_deleted:,} bytes ({immich_deleted_files} files)")
                print(f"  Immich deletion percentage: {immich_delete_pct:.2f}%")
                
                # Trigger warning if Deleted% > 10
                if immich_delete_pct > 10:
                    warning_msg = f"High deletion rate detected in Immich photos ({immich_delete_pct:.2f}% > 10%)"
                    warnings.append(warning_msg)
                    print(f"  WARNING: {warning_msg}")
            else:
                # If B == 0, set Deleted% = 0
                immich_delete_pct = 0
                metrics['immich_delete_pct'] = 0
                print("  Immich BACKUP path is empty (B=0, setting deletion% to 0)")
        else:
            print(f"  Immich paths not found (source: {IMMICH_DATA_PATH.exists()}, backup: {IMMICH_BACKUP_PATH.exists()}), skipping deletion check")
            
    except Exception as e:
        error_msg = f"Immich deletion detection failed: {e}"
        print(f"  WARNING: {error_msg}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        warnings.append(error_msg)
    
    # Nextcloud deletion detector (same rules as Immich - no exclusions)
    print("Checking Nextcloud deletions...")
    try:
        if NEXTCLOUD_DATA_PATH.exists() and NEXTCLOUD_BACKUP_PATH.exists():
            print(f"  Comparing: {NEXTCLOUD_DATA_PATH} -> {NEXTCLOUD_BACKUP_PATH}")
            nextcloud_changed, nextcloud_deleted, nextcloud_deleted_files = calculate_rsync_changes(
                str(NEXTCLOUD_DATA_PATH),
                str(NEXTCLOUD_BACKUP_PATH)
            )
            
            # Calculate total bytes for Nextcloud BACKUP (B = backup_before_bytes)
            # This is the size of BACKUP mirror before rsync runs
            try:
                result = subprocess.run(
                    ['du', '-sb', str(NEXTCLOUD_BACKUP_PATH)],
                    capture_output=True,
                    text=True,
                    check=True
                )
                nextcloud_backup_total = int(result.stdout.split()[0])
            except (subprocess.CalledProcessError, ValueError) as e:
                print(f"WARNING: Could not calculate Nextcloud BACKUP total size: {e}")
                nextcloud_backup_total = 0
            
            # Deleted% = (D × 100) / B
            # D = deleted_bytes (from rsync dry-run), B = backup_before_bytes
            if nextcloud_backup_total > 0:
                nextcloud_delete_pct = (nextcloud_deleted * 100) / nextcloud_backup_total
                metrics['nextcloud_delete_pct'] = round(nextcloud_delete_pct, 2)
                
                print(f"  Nextcloud BACKUP (B): {nextcloud_backup_total:,} bytes")
                print(f"  Nextcloud deleted (D): {nextcloud_deleted:,} bytes ({nextcloud_deleted_files} files)")
                print(f"  Nextcloud deletion percentage: {nextcloud_delete_pct:.2f}%")
                
                # Trigger warning if Deleted% > 10
                if nextcloud_delete_pct > 10:
                    warning_msg = f"High deletion rate detected in Nextcloud files ({nextcloud_delete_pct:.2f}% > 10%)"
                    warnings.append(warning_msg)
                    print(f"  WARNING: {warning_msg}")
            else:
                # If B == 0, set Deleted% = 0
                nextcloud_delete_pct = 0
                metrics['nextcloud_delete_pct'] = 0
                print("  Nextcloud BACKUP path is empty (B=0, setting deletion% to 0)")
        else:
            print(f"  Nextcloud paths not found (source: {NEXTCLOUD_DATA_PATH.exists()}, backup: {NEXTCLOUD_BACKUP_PATH.exists()}), skipping deletion check")
            
    except Exception as e:
        error_msg = f"Nextcloud deletion detection failed: {e}"
        print(f"  WARNING: {error_msg}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        warnings.append(error_msg)
    
    return frozen, warnings, metrics


def update_abnormality_status(frozen, warnings, metrics):
    """
    Update status.json with abnormality detection results.
    """
    try:
        status = read_status()
        
        # Update metrics
        status.update(metrics)
        
        # Update frozen flag and warnings
        status['frozen'] = frozen
        status['warnings'] = warnings if warnings else []
        
        write_status(status)
        
        if frozen:
            print(f"Status updated: FROZEN - Catastrophic change detected")
        elif warnings:
            print(f"Status updated: Warnings: {len(warnings)}")
        else:
            print("Status updated: No abnormalities detected")
    except Exception as e:
        print(f"WARNING: Failed to update abnormality status: {e}", file=sys.stderr)
        # Don't raise - this is informational


def create_daily_snapshot():
    """
    Create a daily snapshot from current/ if it doesn't exist.
    
    Returns:
        tuple: (created: bool, snapshot_name: str or None)
    """
    today = datetime.now(EST_TZ).strftime("%Y-%m-%d")
    snapshot_path = SNAPSHOTS_DAILY_DIR / today
    
    # If snapshot already exists, do nothing (idempotent)
    if snapshot_path.exists():
        print(f"Daily snapshot {today} already exists, skipping creation")
        return False, None
    
    # Ensure daily snapshots directory exists
    SNAPSHOTS_DAILY_DIR.mkdir(parents=True, exist_ok=True)
    
    print(f"Creating daily snapshot: {today}")
    
    # Find the most recent previous snapshot for --link-dest (for space efficiency)
    # If no previous snapshot exists, use current/ as link-dest
    link_dest = str(BACKUP_CURRENT)
    if SNAPSHOTS_DAILY_DIR.exists():
        previous_snapshots = sorted([d.name for d in SNAPSHOTS_DAILY_DIR.iterdir() if d.is_dir()])
        if previous_snapshots:
            # Use the most recent snapshot as link-dest
            link_dest = str(SNAPSHOTS_DAILY_DIR / previous_snapshots[-1])
    
    # Use rsync with hard links to create space-efficient snapshot
    # --link-dest points to the previous snapshot (or current if first)
    # This creates hard links for unchanged files, saving space
    rsync_cmd = [
        'rsync',
        '-aHAX',
        '--numeric-ids',
        '--delete',
        '--link-dest', link_dest,
        f"{BACKUP_CURRENT}/",
        f"{snapshot_path}/"
    ]
    
    try:
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"Daily snapshot {today} created successfully")
        return True, today
    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to create daily snapshot {today}: {e.stderr[:500]}"
        print(f"ERROR: {error_msg}", file=sys.stderr)
        # Clean up partial snapshot if it exists
        if snapshot_path.exists():
            import shutil
            try:
                shutil.rmtree(snapshot_path)
            except Exception:
                pass
        return False, None


def create_weekly_snapshot():
    """
    Create a weekly snapshot from current/ if it doesn't exist.
    Only creates on Sunday (weekday 6).
    
    For testing: Set environment variable chronovault_FORCE_WEEKLY=1 to force creation.
    
    Returns:
        tuple: (created: bool, snapshot_name: str or None)
    """
    now = datetime.now(EST_TZ)
    
    # Only create on Sunday (weekday 6, where Monday=0)
    # Allow override via environment variable for testing
    force_weekly = os.environ.get('chronovault_FORCE_WEEKLY', '0') == '1'
    if not force_weekly and now.weekday() != 6:
        return False, None
    
    # Use Sunday's date as snapshot name: YYYY-MM-DD
    # This is clearer than YYYY-WW (which could be confused with months)
    # Weekly snapshots are always created on Sunday, so use today's date
    snapshot_name = now.strftime("%Y-%m-%d")
    snapshot_path = SNAPSHOTS_WEEKLY_DIR / snapshot_name
    
    # If snapshot already exists, do nothing (idempotent)
    if snapshot_path.exists():
        print(f"Weekly snapshot {snapshot_name} already exists, skipping creation")
        return False, None
    
    # Ensure weekly snapshots directory exists
    SNAPSHOTS_WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    
    print(f"Creating weekly snapshot: {snapshot_name}")
    
    # Find the most recent previous snapshot for --link-dest (for space efficiency)
    # If no previous snapshot exists, use current/ as link-dest
    link_dest = str(BACKUP_CURRENT)
    if SNAPSHOTS_WEEKLY_DIR.exists():
        previous_snapshots = sorted([d.name for d in SNAPSHOTS_WEEKLY_DIR.iterdir() if d.is_dir()])
        if previous_snapshots:
            # Use the most recent snapshot as link-dest
            link_dest = str(SNAPSHOTS_WEEKLY_DIR / previous_snapshots[-1])
    
    # Use rsync with hard links to create space-efficient snapshot
    # --link-dest points to the previous snapshot (or current if first)
    # This creates hard links for unchanged files, saving space
    rsync_cmd = [
        'rsync',
        '-aHAX',
        '--numeric-ids',
        '--delete',
        '--link-dest', link_dest,
        f"{BACKUP_CURRENT}/",
        f"{snapshot_path}/"
    ]
    
    try:
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"Weekly snapshot {snapshot_name} created successfully")
        return True, snapshot_name
    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to create weekly snapshot {snapshot_name}: {e.stderr[:500]}"
        print(f"ERROR: {error_msg}", file=sys.stderr)
        # Clean up partial snapshot if it exists
        if snapshot_path.exists():
            import shutil
            try:
                shutil.rmtree(snapshot_path)
            except Exception:
                pass
        return False, None


def apply_daily_retention(keep_count=14):
    """
    Keep only the most recent N daily snapshots, delete older ones.
    
    Args:
        keep_count: Number of daily snapshots to keep (default: 14)
    
    Returns:
        tuple: (deleted_count: int, remaining_count: int, oldest: str or None)
    """
    if not SNAPSHOTS_DAILY_DIR.exists():
        return 0, 0, None
    
    # Get all daily snapshot directories, sorted by name (YYYY-MM-DD format sorts correctly)
    snapshots = sorted([d.name for d in SNAPSHOTS_DAILY_DIR.iterdir() if d.is_dir()])
    
    if len(snapshots) <= keep_count:
        oldest = snapshots[0] if snapshots else None
        return 0, len(snapshots), oldest
    
    # Delete older snapshots (keep most recent N)
    to_delete = snapshots[:-keep_count]
    deleted_count = 0
    
    for snapshot_name in to_delete:
        snapshot_path = SNAPSHOTS_DAILY_DIR / snapshot_name
        try:
            import shutil
            shutil.rmtree(snapshot_path)
            deleted_count += 1
            print(f"Deleted old daily snapshot: {snapshot_name}")
        except Exception as e:
            print(f"WARNING: Failed to delete daily snapshot {snapshot_name}: {e}", file=sys.stderr)
    
    remaining = snapshots[-keep_count:]
    oldest = remaining[0] if remaining else None
    
    return deleted_count, len(remaining), oldest


def apply_weekly_retention(keep_count=12):
    """
    Keep only the most recent N weekly snapshots, delete older ones.
    
    Args:
        keep_count: Number of weekly snapshots to keep (default: 12)
    
    Returns:
        tuple: (deleted_count: int, remaining_count: int, oldest: str or None)
    """
    if not SNAPSHOTS_WEEKLY_DIR.exists():
        return 0, 0, None
    
    # Get all weekly snapshot directories, sorted by name (YYYY-MM-DD format sorts correctly)
    snapshots = sorted([d.name for d in SNAPSHOTS_WEEKLY_DIR.iterdir() if d.is_dir()])
    
    if len(snapshots) <= keep_count:
        oldest = snapshots[0] if snapshots else None
        return 0, len(snapshots), oldest
    
    # Delete older snapshots (keep most recent N)
    to_delete = snapshots[:-keep_count]
    deleted_count = 0
    
    for snapshot_name in to_delete:
        snapshot_path = SNAPSHOTS_WEEKLY_DIR / snapshot_name
        try:
            import shutil
            shutil.rmtree(snapshot_path)
            deleted_count += 1
            print(f"Deleted old weekly snapshot: {snapshot_name}")
        except Exception as e:
            print(f"WARNING: Failed to delete weekly snapshot {snapshot_name}: {e}", file=sys.stderr)
    
    remaining = snapshots[-keep_count:]
    oldest = remaining[0] if remaining else None
    
    return deleted_count, len(remaining), oldest


def get_backup_disk_usage():
    """
    Get disk usage statistics for backup mount.
    
    Returns:
        tuple: (total_bytes, used_bytes, free_bytes, usage_pct) or None on error
    """
    try:
        usage = shutil.disk_usage(BACKUP_MOUNT)
        total = usage.total
        used = usage.used
        free = usage.free
        usage_pct = (used / total) * 100.0 if total > 0 else 0.0
        return total, used, free, usage_pct
    except Exception as e:
        print(f"WARNING: Could not get backup disk usage: {e}", file=sys.stderr)
        return None


def check_disk_space_low(threshold_pct=10.0):
    """
    Check if backup disk space is below threshold.
    
    Args:
        threshold_pct: Percentage threshold (default: 10%)
    
    Returns:
        tuple: (is_low: bool, free_pct: float) or (False, None) on error
    """
    usage = get_backup_disk_usage()
    if usage is None:
        return False, None
    
    total, used, free, usage_pct = usage
    free_pct = (free / total) * 100.0 if total > 0 else 0.0
    is_low = free_pct < threshold_pct
    
    return is_low, free_pct


def delete_oldest_snapshot(snapshot_type='daily'):
    """
    Delete the single oldest snapshot (for retry logic).
    
    Args:
        snapshot_type: 'daily' or 'weekly'
    
    Returns:
        tuple: (deleted: bool, snapshot_name: str or None, freed_bytes: int)
    """
    if snapshot_type == 'daily':
        snapshots_dir = SNAPSHOTS_DAILY_DIR
    elif snapshot_type == 'weekly':
        snapshots_dir = SNAPSHOTS_WEEKLY_DIR
    else:
        return False, None, 0
    
    if not snapshots_dir.exists():
        return False, None, 0
    
    # Get all snapshots sorted (oldest first)
    snapshots = sorted([d.name for d in snapshots_dir.iterdir() if d.is_dir()])
    
    if not snapshots:
        return False, None, 0
    
    # Always keep at least one snapshot
    if len(snapshots) <= 1:
        return False, None, 0
    
    # Delete the oldest one
    oldest_name = snapshots[0]
    oldest_path = snapshots_dir / oldest_name
    
    try:
        # Calculate size before deletion
        freed_bytes = 0
        try:
            result = subprocess.run(
                ['du', '-sb', str(oldest_path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=30
            )
            freed_bytes = int(result.stdout.split()[0])
        except Exception:
            # Best effort - if we can't calculate, assume we freed space
            freed_bytes = 0
        
        # Delete the snapshot
        shutil.rmtree(oldest_path)
        print(f"Deleted oldest {snapshot_type} snapshot: {oldest_name} (freed ~{freed_bytes:,} bytes)")
        return True, oldest_name, freed_bytes
    except Exception as e:
        print(f"WARNING: Failed to delete oldest {snapshot_type} snapshot {oldest_name}: {e}", file=sys.stderr)
        return False, None, 0


def free_space_aggressively(frozen=False, min_daily=1, min_weekly=1):
    """
    Proactively free space by applying aggressive retention policies.
    Only runs if not frozen (ransomware protection).
    
    Args:
        frozen: If True, do nothing (ransomware protection)
        min_daily: Minimum daily snapshots to keep (default: 1)
        min_weekly: Minimum weekly snapshots to keep (default: 1)
    
    Returns:
        tuple: (freed_bytes: int, deleted_count: int)
    """
    if frozen:
        print("Frozen state detected - skipping aggressive space management (ransomware protection)")
        return 0, 0
    
    freed_bytes = 0
    deleted_count = 0
    
    # Check current space
    is_low, free_pct = check_disk_space_low(threshold_pct=10.0)
    if not is_low:
        return 0, 0
    
    print(f"Low disk space detected ({free_pct:.1f}% free) - applying aggressive retention...")
    
    # First pass: Keep 7 days instead of 14
    try:
        daily_count, _ = get_daily_snapshot_info()
        if daily_count > 7:
            print(f"  Aggressive retention pass 1: Keeping 7 daily snapshots (have {daily_count})")
            daily_deleted, daily_remaining, _ = apply_daily_retention(keep_count=7)
            deleted_count += daily_deleted
            if daily_deleted > 0:
                print(f"  Deleted {daily_deleted} daily snapshot(s), keeping {daily_remaining}")
    except Exception as e:
        print(f"  WARNING: Aggressive retention pass 1 failed: {e}", file=sys.stderr)
    
    # Check space again
    is_low, free_pct = check_disk_space_low(threshold_pct=10.0)
    if not is_low:
        return freed_bytes, deleted_count
    
    # Second pass: Keep 3 days instead of 7
    try:
        daily_count, _ = get_daily_snapshot_info()
        if daily_count > 3:
            print(f"  Aggressive retention pass 2: Keeping 3 daily snapshots (have {daily_count})")
            daily_deleted, daily_remaining, _ = apply_daily_retention(keep_count=3)
            deleted_count += daily_deleted
            if daily_deleted > 0:
                print(f"  Deleted {daily_deleted} daily snapshot(s), keeping {daily_remaining}")
    except Exception as e:
        print(f"  WARNING: Aggressive retention pass 2 failed: {e}", file=sys.stderr)
    
    # Check space again
    is_low, free_pct = check_disk_space_low(threshold_pct=10.0)
    if not is_low:
        return freed_bytes, deleted_count
    
    # Third pass: Keep minimum (1 daily, 1 weekly)
    try:
        daily_count, _ = get_daily_snapshot_info()
        if daily_count > min_daily:
            print(f"  Aggressive retention pass 3: Keeping minimum {min_daily} daily snapshot(s) (have {daily_count})")
            daily_deleted, daily_remaining, _ = apply_daily_retention(keep_count=min_daily)
            deleted_count += daily_deleted
            if daily_deleted > 0:
                print(f"  Deleted {daily_deleted} daily snapshot(s), keeping {daily_remaining}")
        
        weekly_count, _ = get_weekly_snapshot_info()
        if weekly_count > min_weekly:
            print(f"  Aggressive retention pass 3: Keeping minimum {min_weekly} weekly snapshot(s) (have {weekly_count})")
            weekly_deleted, weekly_remaining, _ = apply_weekly_retention(keep_count=min_weekly)
            deleted_count += weekly_deleted
            if weekly_deleted > 0:
                print(f"  Deleted {weekly_deleted} weekly snapshot(s), keeping {weekly_remaining}")
    except Exception as e:
        print(f"  WARNING: Aggressive retention pass 3 failed: {e}", file=sys.stderr)
    
    print(f"Aggressive retention complete: deleted {deleted_count} snapshot(s)")
    return freed_bytes, deleted_count


def perform_mirror_sync_with_retry(frozen=False, max_retries=5):
    """
    Perform mirror sync with automatic retry on space errors.
    Wraps existing perform_mirror_sync() function.
    
    If rsync fails with "no space" (exit code 23) and not frozen:
    - Delete oldest snapshots one at a time
    - Retry rsync
    - Repeat until success or max retries
    
    Args:
        frozen: If True, do not delete snapshots (ransomware protection)
        max_retries: Maximum number of retry attempts (default: 5)
    
    Returns:
        tuple: (success: bool, rsync_exit_code: int, error_message: str)
    """
    # First attempt
    mirror_success, rsync_exit_code, error_message = perform_mirror_sync()
    
    if mirror_success:
        return mirror_success, rsync_exit_code, error_message
    
    # Check if failure was due to space (rsync exit code 23 = "No space left on device")
    if rsync_exit_code != 23:
        # Not a space error, return original failure
        return mirror_success, rsync_exit_code, error_message
    
    # Space error detected
    if frozen:
        print("Frozen state detected - not retrying (ransomware protection)")
        return mirror_success, rsync_exit_code, error_message
    
    print(f"Mirror sync failed due to insufficient space (exit code {rsync_exit_code})")
    print("Attempting to free space by deleting oldest snapshots...")
    
    # Retry loop: delete oldest snapshots and retry
    for attempt in range(1, max_retries + 1):
        print(f"Retry attempt {attempt}/{max_retries}...")
        
        # Try deleting oldest daily snapshot first
        deleted, snapshot_name, freed_bytes = delete_oldest_snapshot('daily')
        if deleted:
            print(f"  Deleted oldest daily snapshot: {snapshot_name} (freed ~{freed_bytes:,} bytes)")
        else:
            # Try weekly if no daily available
            deleted, snapshot_name, freed_bytes = delete_oldest_snapshot('weekly')
            if deleted:
                print(f"  Deleted oldest weekly snapshot: {snapshot_name} (freed ~{freed_bytes:,} bytes)")
            else:
                print("  No more snapshots available to delete (keeping minimum retention)")
                break
        
        # Wait a moment for filesystem to update
        import time
        time.sleep(2)
        
        # Retry rsync
        mirror_success, rsync_exit_code, error_message = perform_mirror_sync()
        
        if mirror_success:
            print(f"Mirror sync succeeded after {attempt} retry attempt(s)")
            return mirror_success, rsync_exit_code, error_message
        
        # If still space error, continue retry loop
        if rsync_exit_code == 23:
            continue
        else:
            # Different error, stop retrying
            print(f"Different error encountered (exit code {rsync_exit_code}), stopping retries")
            break
    
    # All retries exhausted
    print(f"Mirror sync failed after {max_retries} retry attempts")
    return mirror_success, rsync_exit_code, error_message


def update_snapshot_status(daily_created=None, weekly_created=None):
    """
    Update status.json with snapshot information.
    
    Args:
        daily_created: Date string of daily snapshot created (YYYY-MM-DD) or None
        weekly_created: Date string of weekly snapshot created (YYYY-MM-DD, Sunday date) or None
    """
    try:
        status = read_status()
        
        # Update created fields if snapshots were created
        if daily_created:
            status['daily_snapshot_created'] = daily_created
        if weekly_created:
            status['weekly_snapshot_created'] = weekly_created
        
        # Get snapshot counts and oldest dates (post-retention)
        daily_count, daily_oldest = get_daily_snapshot_info()
        weekly_count, weekly_oldest = get_weekly_snapshot_info()
        
        status['daily_snapshot_count'] = daily_count
        status['weekly_snapshot_count'] = weekly_count
        
        if daily_oldest:
            status['oldest_daily_snapshot'] = daily_oldest
        elif 'oldest_daily_snapshot' in status:
            # Keep existing value if no snapshots exist
            pass
        
        if weekly_oldest:
            status['oldest_weekly_snapshot'] = weekly_oldest
        elif 'oldest_weekly_snapshot' in status:
            # Keep existing value if no snapshots exist
            pass
        
        write_status(status)
    except Exception as e:
        print(f"WARNING: Failed to update snapshot status: {e}", file=sys.stderr)
        # Don't raise - snapshot status is informational


def get_daily_snapshot_info():
    """
    Get daily snapshot count and oldest snapshot name.
    
    Returns:
        tuple: (count: int, oldest: str or None)
    """
    if not SNAPSHOTS_DAILY_DIR.exists():
        return 0, None
    
    snapshots = sorted([d.name for d in SNAPSHOTS_DAILY_DIR.iterdir() if d.is_dir()])
    oldest = snapshots[0] if snapshots else None
    return len(snapshots), oldest


def get_weekly_snapshot_info():
    """
    Get weekly snapshot count and oldest snapshot name.
    
    Returns:
        tuple: (count: int, oldest: str or None)
    """
    if not SNAPSHOTS_WEEKLY_DIR.exists():
        return 0, None
    
    snapshots = sorted([d.name for d in SNAPSHOTS_WEEKLY_DIR.iterdir() if d.is_dir()])
    oldest = snapshots[0] if snapshots else None
    return len(snapshots), oldest


def update_backup_disk_usage():
    """
    Record backup disk usage in status.json.
    Called after successful backup to track disk usage even when drive is unmounted.
    """
    try:
        usage = get_backup_disk_usage()
        if usage is None:
            return
        
        total, used, free, usage_pct = usage
        
        try:
            status = read_status()
            status['backup_disk_total_bytes'] = total
            status['backup_disk_used_bytes'] = used
            status['backup_disk_free_bytes'] = free
            status['backup_disk_usage_pct'] = round(usage_pct, 2)
            
            write_status(status)
            print(f"Backup disk usage recorded: {usage_pct:.1f}% used ({used:,} / {total:,} bytes)")
        except Exception as e:
            print(f"WARNING: Failed to write backup disk usage to status: {e}", file=sys.stderr)
            # Don't fail backup if this fails
    except Exception as e:
        print(f"WARNING: Failed to record backup disk usage: {e}", file=sys.stderr)
        # Don't fail backup if this fails


def update_restore_points_index():
    """
    Update restore_points.json with available daily and weekly snapshots.
    This creates an inventory of available restore points that can be read
    by the web UI without mounting the BACKUP disk.
    """
    try:
        restore_points_file = Path("/var/lib/chronovault/restore_points.json")
        
        # Ensure directory exists
        restore_points_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Get available daily snapshots
        daily_snapshots = []
        if SNAPSHOTS_DAILY_DIR.exists():
            daily_snapshots = sorted([d.name for d in SNAPSHOTS_DAILY_DIR.iterdir() if d.is_dir()])
        
        # Get available weekly snapshots
        weekly_snapshots = []
        if SNAPSHOTS_WEEKLY_DIR.exists():
            weekly_snapshots = sorted([d.name for d in SNAPSHOTS_WEEKLY_DIR.iterdir() if d.is_dir()])
        
        # Create restore points data structure
        restore_points = {
            "daily": daily_snapshots,
            "weekly": weekly_snapshots
        }
        
        # Atomic write: write to temp file then rename
        temp_file = restore_points_file.with_suffix('.json.tmp')
        try:
            with open(temp_file, 'w') as f:
                json.dump(restore_points, f, indent=2)
            temp_file.replace(restore_points_file)
            print(f"Restore points index updated: {len(daily_snapshots)} daily, {len(weekly_snapshots)} weekly")
        except Exception as e:
            print(f"WARNING: Failed to update restore points index: {e}", file=sys.stderr)
            # Clean up temp file on error
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
    except Exception as e:
        print(f"WARNING: Failed to update restore points index: {e}", file=sys.stderr)
        # Don't raise - this is informational


def main():
    """Main backup logic: Status reporting + BACKUP identity verification + source size calculation + abnormality detection + baseline mirror sync"""
    try:
        # Update status at start
        update_status_start()
        
        # Step 5.1: Preconditions - Verify BACKUP is mounted
        print("Verifying BACKUP is mounted...")
        if not verify_backup_mounted():
            error_reason = "BACKUP disk is not mounted"
            print(f"ERROR: {error_reason}", file=sys.stderr)
            update_status_error(error_reason)
            return 1
        print("BACKUP mount verified")
        
        # Step 3: Verify BACKUP identity (sanity gate)
        print("Verifying BACKUP identity...")
        if not verify_backup_identity():
            error_reason = "Backup identity missing"
            print(f"ERROR: {error_reason}", file=sys.stderr)
            update_status_error(error_reason)
            return 1
        print("BACKUP identity verified")
        
        # Step 4: Calculate source total bytes (PRIMARY dataset size)
        print("Calculating PRIMARY dataset size...")
        source_total_bytes = calculate_source_total_bytes()
        print(f"Source total bytes: {source_total_bytes:,}")
        
        # Update status.json with source_total_bytes
        try:
            status = read_status()
            status['source_total_bytes'] = source_total_bytes
            write_status(status)
            print("Status updated with source_total_bytes")
        except Exception as e:
            print(f"WARNING: Failed to update source_total_bytes in status: {e}", file=sys.stderr)
            # Continue - this is not critical
        
        # Detect abnormalities BEFORE mirror sync (so we can detect what's about to change)
        frozen, warnings, metrics = detect_abnormalities()
        
        # Check for approval mechanism: if approve_once file exists, override freeze
        approve_override = False
        if APPROVE_ONCE_FILE.exists():
            print("Approval file detected - overriding freeze protection")
            # Delete both frozen and approve_once files
            if FREEZE_FLAG_FILE.exists():
                try:
                    FREEZE_FLAG_FILE.unlink()
                    print(f"Deleted freeze flag file: {FREEZE_FLAG_FILE}")
                except Exception as e:
                    print(f"WARNING: Could not delete freeze flag file: {e}", file=sys.stderr)
            try:
                APPROVE_ONCE_FILE.unlink()
                print(f"Deleted approval file: {APPROVE_ONCE_FILE}")
            except Exception as e:
                print(f"WARNING: Could not delete approval file: {e}", file=sys.stderr)
            approve_override = True
            # Clear frozen status in status.json
            frozen = False
        
        # Update status with abnormality detection results
        try:
            update_abnormality_status(frozen, warnings, metrics)
        except Exception as e:
            print(f"WARNING: Failed to update abnormality status: {e}", file=sys.stderr)
            # Continue - abnormality detection is important but not critical for backup to proceed
        
        # Check if frozen (unless overridden by approval)
        if frozen and not approve_override:
            error_reason = "Catastrophic change detected - backup frozen"
            print(f"ERROR: {error_reason}", file=sys.stderr)
            print(f"To approve this backup, create: {APPROVE_ONCE_FILE}", file=sys.stderr)
            update_status_error(error_reason)
            return 1
        
        # Proactive space management: check disk space before rsync
        is_low, free_pct = check_disk_space_low(threshold_pct=10.0)
        if is_low and not frozen:
            print(f"Low disk space detected ({free_pct:.1f}% free) - applying proactive space management...")
            freed_bytes, deleted_count = free_space_aggressively(frozen=frozen)
            if deleted_count > 0:
                print(f"Proactive space management: freed space by deleting {deleted_count} snapshot(s)")
        
        # Perform baseline mirror sync (with retry logic for space errors)
        print("Starting mirror sync...")
        mirror_success, rsync_exit_code, error_message = perform_mirror_sync_with_retry(frozen=frozen)
        
        # Update mirror status
        update_mirror_status(mirror_success, rsync_exit_code, error_message)
        
        if not mirror_success:
            error_reason = f"Mirror sync failed: {error_message}"
            print(f"ERROR: {error_reason}", file=sys.stderr)
            update_status_error(error_reason)
            return 1
        
        print("Mirror sync completed successfully")
        
        # Step 7: Create snapshots (only if not frozen)
        # Preconditions already met: BACKUP mounted, IDENTITY verified, not frozen
        # Additional check: current/ must exist
        if not BACKUP_CURRENT.exists():
            print("WARNING: current/ does not exist, skipping snapshot creation")
        else:
            print("Creating snapshots...")
            
            daily_created = None
            weekly_created = None
            
            try:
                # Create daily snapshot (every successful backup run)
                daily_created_bool, daily_snapshot_name = create_daily_snapshot()
                if daily_created_bool:
                    daily_created = daily_snapshot_name
            except Exception as e:
                print(f"WARNING: Daily snapshot creation failed (continuing): {e}", file=sys.stderr)
            
            try:
                # Create weekly snapshot (only on Sunday)
                weekly_created_bool, weekly_snapshot_name = create_weekly_snapshot()
                if weekly_created_bool:
                    weekly_created = weekly_snapshot_name
            except Exception as e:
                print(f"WARNING: Weekly snapshot creation failed (continuing): {e}", file=sys.stderr)
            
            try:
                # Apply retention policies
                print("Applying retention policies...")
                daily_deleted, daily_remaining, daily_oldest = apply_daily_retention(keep_count=14)
                if daily_deleted > 0:
                    print(f"Daily retention: deleted {daily_deleted} old snapshot(s), keeping {daily_remaining}")
                
                weekly_deleted, weekly_remaining, weekly_oldest = apply_weekly_retention(keep_count=12)
                if weekly_deleted > 0:
                    print(f"Weekly retention: deleted {weekly_deleted} old snapshot(s), keeping {weekly_remaining}")
            except Exception as e:
                print(f"WARNING: Retention policy application failed (continuing): {e}", file=sys.stderr)
            
            try:
                # Update status with snapshot information
                update_snapshot_status(daily_created=daily_created, weekly_created=weekly_created)
            except Exception as e:
                print(f"WARNING: Failed to update snapshot status (continuing): {e}", file=sys.stderr)
            
            try:
                # Update restore points index (inventory of available restore points)
                update_restore_points_index()
            except Exception as e:
                print(f"WARNING: Failed to update restore points index (continuing): {e}", file=sys.stderr)
        
        # Update status on success
        update_status_success()
        
        # Record backup disk usage for monitoring (even when drive is unmounted)
        update_backup_disk_usage()
        
        return 0
        
    except Exception as e:
        error_msg = f"Unhandled exception: {e}"
        print(error_msg, file=sys.stderr)
        update_status_error(error_msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
