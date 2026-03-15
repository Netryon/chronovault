#!/usr/bin/env python3
"""
chronovault Restore - Python Logic
Step 8: Deterministic restore workflows for files and databases
"""

import sys
import json
import os
import subprocess
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# EST timezone (handles EST/EDT automatically)
EST_TZ = ZoneInfo("America/New_York")

STATUS_FILE = Path("/var/lib/chronovault/status.json")
LOG_FILE = Path("/var/log/chronovault/backup.log")
PRIMARY_MOUNT = Path("/mnt/primary")
BACKUP_MOUNT = Path("/mnt/backup")
BACKUP_IDENTITY_FILE = BACKUP_MOUNT / "chronovault" / "metadata" / "IDENTITY"
BACKUP_CURRENT = BACKUP_MOUNT / "chronovault" / "current"
SNAPSHOTS_DAILY_DIR = BACKUP_MOUNT / "chronovault" / "snapshots" / "daily"
SNAPSHOTS_WEEKLY_DIR = BACKUP_MOUNT / "chronovault" / "snapshots" / "weekly"
# DB dumps are in the snapshot under backups/db/
DB_DUMPS_DIR = "backups/db"

# Restore targets (match backup source paths)
IMMICH_FILES_TARGET = PRIMARY_MOUNT / "apps" / "immich" / "upload" / "upload"
NEXTCLOUD_FILES_TARGET = PRIMARY_MOUNT / "apps" / "nextcloud" / "data"

# Container names
IMMICH_POSTGRES_CONTAINER = "immich-postgres"
NEXTCLOUD_POSTGRES_CONTAINER = "nextcloud-postgres"


def log(message):
    """Append message to log file with timestamp."""
    timestamp = datetime.now(EST_TZ).strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] RESTORE: {message}\n"
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(log_entry)
    except Exception:
        pass  # Best effort
    print(message, flush=True)


def read_status():
    """Read status.json, return dict or empty dict."""
    try:
        if STATUS_FILE.exists():
            with open(STATUS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def write_status(status):
    """Atomically write status.json."""
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = STATUS_FILE.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(status, f, indent=2)
        temp_file.replace(STATUS_FILE)
    except Exception as e:
        log(f"ERROR: Failed to write status.json: {e}")
        raise


def update_restore_status(restore_type, restore_source):
    """Update status.json with restore information."""
    status = read_status()
    now = datetime.now(EST_TZ).isoformat()
    status['last_restore_time'] = now
    status['last_restore_type'] = restore_type  # 'files', 'db', 'full'
    status['last_restore_source'] = str(restore_source)
    write_status(status)


def verify_backup_mounted():
    """Verify BACKUP is mounted."""
    return BACKUP_MOUNT.exists() and BACKUP_MOUNT.is_mount()


def verify_backup_identity():
    """Verify BACKUP identity file exists."""
    return BACKUP_IDENTITY_FILE.exists()


def verify_snapshot_path(snapshot_path):
    """Verify snapshot path exists and is a directory."""
    path = Path(snapshot_path)
    if not path.exists():
        raise ValueError(f"Snapshot path does not exist: {snapshot_path}")
    if not path.is_dir():
        raise ValueError(f"Snapshot path is not a directory: {snapshot_path}")
    return path


def stop_container(container_name):
    """Stop a Docker container."""
    log(f"Stopping container: {container_name}")
    try:
        result = subprocess.run(
            ['docker', 'stop', '-t', '30', container_name],
            capture_output=True,
            text=True,
            check=True
        )
        log(f"Container {container_name} stopped successfully")
        return True
    except subprocess.CalledProcessError as e:
        log(f"WARNING: Failed to stop container {container_name}: {e.stderr}")
        return False


def restart_container(container_name):
    """Restart a Docker container (stop then start for clean restart)."""
    log(f"Restarting container: {container_name}")
    try:
        # First ensure it's stopped
        subprocess.run(
            ['docker', 'stop', '-t', '30', container_name],
            capture_output=True,
            text=True,
            check=False  # Don't fail if already stopped
        )
        # Then start it
        result = subprocess.run(
            ['docker', 'start', container_name],
            capture_output=True,
            text=True,
            check=True
        )
        log(f"Container {container_name} restarted successfully")
        return True
    except subprocess.CalledProcessError as e:
        log(f"ERROR: Failed to restart container {container_name}: {e.stderr}")
        return False


def get_container_user_id(container_name):
    """Get the UID and GID of the user running in a container."""
    try:
        # Try to get UID from container
        result = subprocess.run(
            ['docker', 'exec', container_name, 'id', '-u'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        uid = int(result.stdout.strip())
        
        result = subprocess.run(
            ['docker', 'exec', container_name, 'id', '-g'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        gid = int(result.stdout.strip())
        
        return uid, gid
    except Exception:
        # Default fallback
        return 1000, 1000


def fix_permissions(path, container_name=None):
    """Fix file permissions after restore (best effort)."""
    try:
        path_obj = Path(path)
        if not path_obj.exists():
            log(f"WARNING: Path does not exist for permission fix: {path}")
            return
        
        # Try to get UID/GID from container if provided
        uid, gid = 1000, 1000  # Default fallback
        if container_name:
            try:
                uid, gid = get_container_user_id(container_name)
                log(f"Detected container {container_name} running as UID:{uid}, GID:{gid}")
            except Exception as e:
                log(f"WARNING: Could not get UID/GID from container {container_name}: {e}, using default 1000:1000")
        
        log(f"Fixing permissions for {path} (UID:{uid}, GID:{gid})")
        
        # Use chown to set ownership
        result = subprocess.run(
            ['chown', '-R', f'{uid}:{gid}', str(path_obj)],
            capture_output=True,
            text=True,
            check=False  # Best effort, don't fail restore if this fails
        )
        if result.returncode != 0:
            log(f"WARNING: chown failed: {result.stderr}")
        else:
            log(f"Successfully changed ownership to {uid}:{gid}")
        
        # Ensure directories are readable/executable
        result = subprocess.run(
            ['chmod', '-R', 'u+rwX,go+rX', str(path_obj)],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            log(f"WARNING: chmod failed: {result.stderr}")
        else:
            log(f"Successfully set permissions")
        
        # Verify the fix worked
        try:
            stat_result = subprocess.run(
                ['stat', '-c', '%U:%G (%u:%g)', str(path_obj)],
                capture_output=True,
                text=True,
                check=True
            )
            log(f"Verification - top-level ownership: {stat_result.stdout.strip()}")
        except Exception:
            pass
            
    except Exception as e:
        log(f"WARNING: Could not fix permissions for {path}: {e}")
        import traceback
        log(f"Traceback: {traceback.format_exc()}")


def stop_all_app_containers():
    """Stop all application containers (Immich and Nextcloud)."""
    stopped = []
    
    # Get all running containers
    try:
        result = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            check=True
        )
        running_containers = result.stdout.strip().split('\n') if result.stdout.strip() else []
    except Exception as e:
        log(f"WARNING: Could not list running containers: {e}")
        running_containers = []
    
    # Stop all containers that match Immich or Nextcloud patterns
    containers_to_stop = []
    for container in running_containers:
        if any(pattern in container.lower() for pattern in ['immich', 'nextcloud']):
            containers_to_stop.append(container)
    
    # Stop each container
    for container in containers_to_stop:
        if stop_container(container):
            stopped.append(container)
    
    return stopped


def restore_files_from_snapshot(source_snapshot, target_path, app_name):
    """
    Restore files from snapshot to target path using rsync.
    
    Args:
        source_snapshot: Path to snapshot directory
        target_path: Target path on PRIMARY
        app_name: Name of app (for logging)
    """
    source = Path(source_snapshot)
    target = Path(target_path)
    
    log(f"Restoring {app_name} files from {source} to {target}")
    
    # Verify source exists and has content
    if not source.exists():
        raise ValueError(f"Source path does not exist: {source}")
    
    # Count files in source for logging
    try:
        file_count = sum(1 for _ in source.rglob('*') if _.is_file())
        log(f"Source contains approximately {file_count} files")
    except Exception:
        pass
    
    # Ensure target parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)
    
    # Build rsync command: -a (archive), --delete (mirror), --numeric-ids, --info for progress
    # Use --numeric-ids to preserve ownership from snapshot
    # Note: This requires root/sudo to set ownership properly
    rsync_cmd = [
        'rsync',
        '-aHAX',
        '--numeric-ids',  # Preserve numeric UID/GID from source
        '--delete',
        '--info=progress2,stats2',
        f"{source}/",
        f"{target}/"
    ]
    
    try:
        log(f"Running rsync: {' '.join(rsync_cmd)}")
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        # Log rsync output for debugging
        if result.stdout:
            log(f"Rsync output: {result.stdout[-500:]}")  # Last 500 chars
        
        # Verify files were copied
        try:
            target_file_count = sum(1 for _ in target.rglob('*') if _.is_file())
            log(f"Target now contains approximately {target_file_count} files")
        except Exception:
            pass
        
        log(f"{app_name} files restored successfully")
        return True
    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to restore {app_name} files: {e.stderr[:500]}"
        log(f"ERROR: {error_msg}")
        if e.stdout:
            log(f"Rsync stdout: {e.stdout[-500:]}")
        raise RuntimeError(error_msg) from e


def find_dump_for_date(snapshot_path, app_name, target_date):
    """
    Find the best dump file for a given date in snapshot.
    Prefers same-day dump, falls back to nearest earlier dump.
    For weekly snapshots, uses most recent dump in that snapshot.
    
    Args:
        snapshot_path: Path to snapshot directory
        app_name: 'immich' or 'nextcloud'
        target_date: Target date (datetime object)
    
    Returns:
        Path to dump file or None
    """
    dump_path = Path(snapshot_path) / DB_DUMPS_DIR
    if not dump_path.exists():
        return None
    
    # Look for dumps matching the date pattern
    # Format: immich_db_YYYYMMDDTHHMMSSZ.dump or nextcloud_db_YYYYMMDDTHHMMSSZ.dump
    target_date_str = target_date.strftime("%Y%m%d")
    
    # Find all dump files for this app
    pattern = f"{app_name}_db_*.dump"
    dumps = sorted(dump_path.glob(pattern), reverse=True)
    
    if not dumps:
        return None
    
    # For weekly snapshots, use the most recent dump (snapshots are created on Sunday)
    # Check if this is a weekly snapshot by checking parent directory name
    snapshot_parent = Path(snapshot_path).parent.name if Path(snapshot_path).parent else ""
    if snapshot_parent == 'weekly':
        log(f"Weekly snapshot detected, using most recent dump: {dumps[0].name}")
        return dumps[0]
    
    # For daily snapshots, prefer same-day dump
    for dump in dumps:
        if target_date_str in dump.name:
            return dump
    
    # Fallback to nearest earlier dump
    for dump in dumps:
        dump_date_str = dump.name.split('_db_')[1].split('T')[0] if '_db_' in dump.name else ""
        if dump_date_str and dump_date_str <= target_date_str:
            return dump
    
    # If no earlier dump found, use most recent
    return dumps[0] if dumps else None


def wait_for_postgres_ready(container_name, max_wait=30):
    """
    Wait for PostgreSQL container to be ready to accept connections.
    
    Args:
        container_name: Docker container name
        max_wait: Maximum seconds to wait (default: 30)
    """
    import time
    log(f"Waiting for {container_name} to be ready...")
    
    # Get PostgreSQL user from container
    pg_user = get_postgres_user(container_name)
    
    start_time = time.time()
    while time.time() - start_time < max_wait:
        # Try to connect using TCP (127.0.0.1) which is more reliable in containers
        result = subprocess.run(
            ['docker', 'exec', '-i', container_name, 'pg_isready', '-h', '127.0.0.1', '-U', pg_user],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            log(f"{container_name} is ready")
            return True
        
        time.sleep(2)
    
    # If pg_isready doesn't work, try a simple psql connection
    log(f"pg_isready failed, trying direct psql connection...")
    result = subprocess.run(
        ['docker', 'exec', '-i', container_name, 'psql', '-h', '127.0.0.1', '-U', pg_user, '-c', 'SELECT 1;'],
        capture_output=True,
        text=True,
        check=False
    )
    if result.returncode == 0:
        log(f"{container_name} is ready (verified via psql)")
        return True
    
    raise RuntimeError(f"{container_name} did not become ready within {max_wait} seconds")


def get_postgres_user(container_name):
    """
    Get PostgreSQL username from container environment.
    Falls back to 'postgres' if not found.
    """
    try:
        result = subprocess.run(
            ['docker', 'exec', '-i', container_name, 'sh', '-lc', 'printf "%s" "${POSTGRES_USER:-postgres}"'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        pg_user = result.stdout.strip()
        if pg_user:
            return pg_user
    except Exception as e:
        log(f"WARNING: Could not detect PostgreSQL user from container, using 'postgres': {e}")
    return 'postgres'


def get_nextcloud_db_user():
    """
    Get the database user from Nextcloud's config.php.
    Returns the dbuser value or None if not found.
    """
    config_path = Path("/mnt/primary/apps/nextcloud/html/config/config.php")
    if not config_path.exists():
        return None
    
    try:
        with open(config_path, 'r') as f:
            content = f.read()
            # Look for 'dbuser' => 'value'
            import re
            match = re.search(r"'dbuser'\s*=>\s*'([^']+)'", content)
            if match:
                return match.group(1)
    except Exception as e:
        log(f"WARNING: Could not read Nextcloud config.php: {e}")
    return None


def fix_nextcloud_db_permissions(container_name, db_name, pg_admin_user):
    """
    Fix Nextcloud database permissions after restore.
    Grants all privileges to the user specified in config.php.
    """
    log("Fixing Nextcloud database permissions...")
    
    # Get the database user from config.php
    nc_db_user = get_nextcloud_db_user()
    if not nc_db_user:
        log("WARNING: Could not determine Nextcloud database user from config.php")
        log("Database permissions may be incorrect - Nextcloud may not work")
        return
    
    log(f"Nextcloud config.php specifies database user: {nc_db_user}")
    
    # If the user is the same as the admin user, permissions should already be correct
    if nc_db_user == pg_admin_user:
        log(f"Database user matches admin user ({pg_admin_user}), permissions should be correct")
        return
    
    # Connect to postgres database to grant permissions
    psql_base = ['docker', 'exec', '-i', container_name, 'psql', '-h', '127.0.0.1', '-U', pg_admin_user, '-d', 'postgres']
    
    try:
        # Ensure the user exists (create if it doesn't)
        log(f"Ensuring database user {nc_db_user} exists...")
        create_user_result = subprocess.run(
            psql_base + ['-c', f"DO $$ BEGIN CREATE USER {nc_db_user}; EXCEPTION WHEN duplicate_object THEN null; END $$;"],
            capture_output=True,
            text=True,
            check=False
        )
        
        # Grant all privileges on the database
        log(f"Granting privileges on database {db_name} to {nc_db_user}...")
        grant_db_result = subprocess.run(
            psql_base + ['-c', f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {nc_db_user};"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Connect to the target database to grant schema and table privileges
        psql_db = ['docker', 'exec', '-i', container_name, 'psql', '-h', '127.0.0.1', '-U', pg_admin_user, '-d', db_name]
        
        # Grant usage on schema
        log(f"Granting schema privileges to {nc_db_user}...")
        subprocess.run(
            psql_db + ['-c', f"GRANT USAGE ON SCHEMA public TO {nc_db_user};"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Grant all privileges on all tables
        log(f"Granting table privileges to {nc_db_user}...")
        grant_tables_result = subprocess.run(
            psql_db + ['-c', f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {nc_db_user};"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Grant privileges on sequences
        log(f"Granting sequence privileges to {nc_db_user}...")
        subprocess.run(
            psql_db + ['-c', f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {nc_db_user};"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Set default privileges for future objects
        log(f"Setting default privileges for {nc_db_user}...")
        subprocess.run(
            psql_db + ['-c', f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {nc_db_user};"],
            capture_output=True,
            text=True,
            check=True
        )
        subprocess.run(
            psql_db + ['-c', f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {nc_db_user};"],
            capture_output=True,
            text=True,
            check=True
        )
        
        log(f"Nextcloud database permissions fixed successfully for user {nc_db_user}")
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr[:300] if e.stderr else str(e)
        log(f"ERROR: Failed to fix database permissions: {error_msg}")
        log("Nextcloud may not work correctly - manual permission fix may be required")
        raise


def restore_database(container_name, dump_file, db_name, app_name):
    """
    Restore a PostgreSQL database from dump file.
    
    Args:
        container_name: Docker container name
        dump_file: Path to .dump file
        db_name: Database name
        app_name: App name (for logging)
    """
    dump_path = Path(dump_file)
    if not dump_path.exists():
        raise ValueError(f"Dump file does not exist: {dump_file}")
    
    log(f"Restoring {app_name} database from {dump_path.name}")
    
    # Check if container exists and is running
    try:
        # Check if container exists
        result = subprocess.run(
            ['docker', 'ps', '-a', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            check=True
        )
        if container_name not in result.stdout:
            raise ValueError(f"Container {container_name} does not exist")
        
        # Check if container is running
        result = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            check=True
        )
        if container_name not in result.stdout:
            log(f"Container {container_name} is not running, attempting to start it...")
            start_result = subprocess.run(
                ['docker', 'start', container_name],
                capture_output=True,
                text=True,
                check=True
            )
            log(f"Container {container_name} started, waiting for it to be ready...")
            wait_for_postgres_ready(container_name, max_wait=30)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to check/start container: {e.stderr}") from e
    
    # Detect PostgreSQL user from container environment
    pg_user = get_postgres_user(container_name)
    log(f"Using PostgreSQL user: {pg_user}")
    
    # Drop and recreate database
    log(f"Dropping and recreating database {db_name} in {container_name}")
    try:
        import time
        
        # Connect to 'postgres' database (not the target database) to avoid "cannot drop currently open database" error
        # Use TCP connection (127.0.0.1) instead of socket - more reliable in containers
        psql_base = ['docker', 'exec', '-i', container_name, 'psql', '-h', '127.0.0.1', '-U', pg_user, '-d', 'postgres']
        
        # If 'postgres' database doesn't exist, try 'template1'
        check_postgres = subprocess.run(
            ['docker', 'exec', '-i', container_name, 'psql', '-h', '127.0.0.1', '-U', pg_user, '-d', 'postgres', '-c', 'SELECT 1;'],
            capture_output=True,
            text=True,
            check=False
        )
        if check_postgres.returncode != 0:
            # Try template1 instead
            psql_base = ['docker', 'exec', '-i', container_name, 'psql', '-h', '127.0.0.1', '-U', pg_user, '-d', 'template1']
            log("Using template1 database for connection")
        
        # First, terminate all active connections to the target database
        log(f"Terminating active connections to database {db_name}...")
        terminate_result = subprocess.run(
            psql_base + ['-c', 
             f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid();"],
            capture_output=True,
            text=True,
            check=False
        )
        if terminate_result.returncode == 0:
            log(f"Terminated connections to {db_name}")
        else:
            log(f"WARNING: Could not terminate connections: {terminate_result.stderr[:200]}")
        
        # Wait for connections to close
        time.sleep(2)
        
        # Drop database (must connect to a different database first)
        log(f"Dropping database {db_name}...")
        drop_result = subprocess.run(
            psql_base + ['-c', f'DROP DATABASE IF EXISTS {db_name};'],
            capture_output=True,
            text=True,
            check=False
        )
        if drop_result.returncode != 0:
            error_msg = drop_result.stderr[:300] if drop_result.stderr else drop_result.stdout[:300]
            log(f"WARNING: Drop database command had issues: {error_msg}")
            # If drop failed but database might still exist, try to continue anyway
        else:
            log(f"Database {db_name} dropped successfully")
        
        # Wait a moment for database to be fully dropped
        time.sleep(2)
        
        # Check if database still exists
        check_result = subprocess.run(
            psql_base + ['-c', f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';"],
            capture_output=True,
            text=True,
            check=False
        )
        if check_result.returncode == 0 and '1' in check_result.stdout:
            log(f"WARNING: Database {db_name} still exists after drop attempt, forcing drop...")
            # Try one more time with force
            subprocess.run(
                psql_base + ['-c', f'DROP DATABASE {db_name};'],
                capture_output=True,
                text=True,
                check=False
            )
            time.sleep(2)
        
        # Create database - use template0 to avoid collation conflicts
        log(f"Creating database {db_name}...")
        create_result = subprocess.run(
            psql_base + ['-c', 
             f"CREATE DATABASE {db_name} WITH ENCODING 'UTF8' TEMPLATE template0;"],
            capture_output=True,
            text=True,
            check=True
        )
        log(f"Database {db_name} created successfully")
    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to recreate database: {e.stderr[:500]}"
        log(f"ERROR: {error_msg}")
        raise RuntimeError(error_msg) from e
    
    # Restore from dump
    log(f"Restoring database from dump file...")
    try:
        # Verify dump file is valid before restoring
        log(f"Verifying dump file integrity...")
        verify_result = subprocess.run(
            ['docker', 'exec', '-i', container_name, 'pg_restore', '-l'],
            stdin=open(dump_path, 'rb'),
            capture_output=True,
            text=True,
            check=False
        )
        if verify_result.returncode != 0:
            raise RuntimeError(f"Dump file appears invalid: {verify_result.stderr[:200]}")
        log(f"Dump file verified, proceeding with restore...")
        
        # Restore with verbose output and error handling
        # Note: pg_restore reads from stdin, but we still need connection params
        with open(dump_path, 'rb') as f:
            result = subprocess.run(
                ['docker', 'exec', '-i', container_name, 'pg_restore', 
                 '-h', '127.0.0.1',
                 '-U', pg_user, 
                 '-d', db_name, 
                 '--no-owner', 
                 '--no-acl',
                 '--verbose',
                 '--exit-on-error'],
                stdin=f,
                capture_output=True,
                text=True,
                check=True
            )
        
        # Log restore statistics
        if result.stdout:
            # Extract key info from restore output
            lines = result.stdout.split('\n')
            for line in lines[-20:]:  # Last 20 lines
                if 'ERROR' in line or 'WARNING' in line or 'restoring' in line.lower():
                    log(f"Restore: {line[:200]}")
        
        log(f"{app_name} database restored successfully")
        
        # Fix permissions: grant access to the user that Nextcloud/Immich actually uses
        if app_name == 'Nextcloud':
            fix_nextcloud_db_permissions(container_name, db_name, pg_user)
        elif app_name == 'Immich':
            # Immich uses the same user as the dump, so permissions should be fine
            log(f"Immich database permissions should be correct (using {pg_user})")
        
        # Verify database has content
        verify_result = subprocess.run(
            ['docker', 'exec', '-i', container_name, 'psql', '-h', '127.0.0.1', '-U', pg_user, '-d', db_name, '-c', 
             "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';"],
            capture_output=True,
            text=True,
            check=True
        )
        table_count = verify_result.stdout.strip().split('\n')[-1] if verify_result.stdout else "unknown"
        log(f"Database verification: {table_count} tables found in public schema")
        
        return True
    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to restore {app_name} database: {e.stderr[:500]}"
        log(f"ERROR: {error_msg}")
        if e.stdout:
            log(f"Restore stdout (last 500 chars): {e.stdout[-500:]}")
        raise RuntimeError(error_msg) from e
    except FileNotFoundError:
        raise RuntimeError(f"Dump file not found: {dump_path}")


# Removed restore_files_only and restore_database_only functions
# Only full restore is supported to ensure files and database stay in sync


def restore_full(snapshot_path, apps=None):
    """
    Full restore: files + databases from snapshot.
    This is the only restore type - ensures files and database stay in sync.
    
    Args:
        snapshot_path: Path to snapshot (daily or weekly)
        apps: List of apps to restore ('immich', 'nextcloud', or None for both)
    """
    if apps is None:
        apps = ['immich', 'nextcloud']
    
    snapshot = verify_snapshot_path(snapshot_path)
    
    # Extract date from snapshot path
    snapshot_name = snapshot.name
    snapshot_date = None
    
    # Try to parse date from snapshot name
    if snapshot.parent.name == 'daily':
        # Format: YYYY-MM-DD
        try:
            snapshot_date = datetime.strptime(snapshot_name, "%Y-%m-%d")
        except ValueError:
            pass
    elif snapshot.parent.name == 'weekly':
        # Format: YYYY-MM-DD (Sunday date) - new format
        # Also supports YYYY-WW for backwards compatibility with old snapshots
        try:
            # Try new format first: YYYY-MM-DD
            snapshot_date = datetime.strptime(snapshot_name, "%Y-%m-%d")
            snapshot_date = snapshot_date.replace(tzinfo=EST_TZ)
        except ValueError:
            # Fallback to old format: YYYY-WW (for backwards compatibility)
            try:
                year, week = snapshot_name.split('-')
                year = int(year)
                week = int(week)
                # Calculate first day of ISO week
                # ISO week 1 is the week containing Jan 4
                jan4 = datetime(year, 1, 4, tzinfo=EST_TZ)
                jan4_weekday = jan4.weekday()  # Monday=0, Sunday=6
                # ISO week starts on Monday
                days_to_monday = (jan4_weekday) % 7
                jan1_monday = jan4 - timedelta(days=days_to_monday)
                # Calculate the Monday of the target week
                week1_monday = jan1_monday - timedelta(weeks=1) if jan1_monday.weekday() != 0 else jan1_monday
                target_monday = week1_monday + timedelta(weeks=week-1)
                snapshot_date = target_monday
                log(f"Using old weekly snapshot format (YYYY-WW): {snapshot_name}")
            except (ValueError, IndexError) as e:
                log(f"WARNING: Could not parse weekly snapshot date from {snapshot_name}: {e}")
                pass
    
    if not snapshot_date:
        # Fallback: use current date
        snapshot_date = datetime.now(EST_TZ)
        log(f"WARNING: Could not parse date from snapshot name, using current date")
    
    log(f"Starting full restore from {snapshot} (date: {snapshot_date.strftime('%Y-%m-%d')})")
    log("Full restore ensures files and database stay in sync")
    
    # Stop containers
    stopped = stop_all_app_containers()
    log(f"Stopped {len(stopped)} container(s)")
    
    try:
        # Step 1: Restore files
        log("Step 1: Restoring files...")
        if 'immich' in apps:
            immich_source = snapshot / "apps" / "immich" / "upload" / "upload"
            if immich_source.exists():
                restore_files_from_snapshot(immich_source, IMMICH_FILES_TARGET, "Immich")
            else:
                log(f"WARNING: Immich source path not found in snapshot: {immich_source}")
        
        if 'nextcloud' in apps:
            nextcloud_source = snapshot / "apps" / "nextcloud" / "data"
            if nextcloud_source.exists():
                restore_files_from_snapshot(nextcloud_source, NEXTCLOUD_FILES_TARGET, "Nextcloud")
            else:
                log(f"WARNING: Nextcloud source path not found in snapshot: {nextcloud_source}")
        
        # Step 2: Start database containers needed for restore
        log("Step 2: Starting database containers for restore...")
        db_containers_to_start = []
        if 'immich' in apps:
            db_containers_to_start.append(IMMICH_POSTGRES_CONTAINER)
        if 'nextcloud' in apps:
            db_containers_to_start.append(NEXTCLOUD_POSTGRES_CONTAINER)
        
        for db_container in db_containers_to_start:
            if db_container in stopped:
                log(f"Starting database container: {db_container}")
                try:
                    subprocess.run(['docker', 'start', db_container], check=True, capture_output=True)
                    log(f"Container {db_container} started successfully")
                except subprocess.CalledProcessError as e:
                    log(f"WARNING: Failed to start {db_container}: {e.stderr.decode() if e.stderr else 'unknown error'}")
                    raise RuntimeError(f"Failed to start database container {db_container}") from e
        
        # Wait for database containers to be ready
        import time
        log("Waiting for database containers to be ready...")
        for db_container in db_containers_to_start:
            wait_for_postgres_ready(db_container, max_wait=30)
        
        # Step 3: Restore databases (must match files date)
        log("Step 3: Restoring databases...")
        if 'immich' in apps:
            immich_dump = find_dump_for_date(snapshot, 'immich', snapshot_date)
            if immich_dump:
                log(f"Found Immich dump: {immich_dump.name}")
                restore_database(IMMICH_POSTGRES_CONTAINER, immich_dump, 'immich', 'Immich')
            else:
                log(f"WARNING: No Immich dump found for date {snapshot_date.strftime('%Y-%m-%d')}")
                log("Immich files restored but database not restored - files may not appear until scan")
        
        if 'nextcloud' in apps:
            nextcloud_dump = find_dump_for_date(snapshot, 'nextcloud', snapshot_date)
            if nextcloud_dump:
                log(f"Found Nextcloud dump: {nextcloud_dump.name}")
                restore_database(NEXTCLOUD_POSTGRES_CONTAINER, nextcloud_dump, 'nextcloud', 'Nextcloud')
            else:
                log(f"WARNING: No Nextcloud dump found for date {snapshot_date.strftime('%Y-%m-%d')}")
                log("Nextcloud files restored but database not restored - may cause issues")
        
        # Step 4: Fix permissions after restore
        log("Step 4: Fixing file permissions...")
        if 'immich' in apps:
            log("Fixing Immich file permissions...")
            try:
                snapshot_immich = snapshot / "apps" / "immich" / "upload" / "upload"
                if snapshot_immich.exists():
                    result = subprocess.run(
                        ['stat', '-c', '%u:%g', str(snapshot_immich)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    uid, gid = result.stdout.strip().split(':')
                    log(f"Preserving Immich ownership from snapshot: {uid}:{gid}")
                    subprocess.run(['chown', '-R', f'{uid}:{gid}', str(IMMICH_FILES_TARGET)], check=False)
            except Exception as e:
                log(f"WARNING: Could not preserve Immich ownership: {e}")
                subprocess.run(['chown', '-R', '0:0', str(IMMICH_FILES_TARGET)], check=False)
        
        if 'nextcloud' in apps:
            log("Fixing Nextcloud file permissions...")
            try:
                snapshot_nextcloud = snapshot / "apps" / "nextcloud" / "data"
                if snapshot_nextcloud.exists():
                    result = subprocess.run(
                        ['stat', '-c', '%u:%g', str(snapshot_nextcloud)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    uid, gid = result.stdout.strip().split(':')
                    log(f"Preserving Nextcloud ownership from snapshot: {uid}:{gid}")
                    subprocess.run(['chown', '-R', f'{uid}:{gid}', str(NEXTCLOUD_FILES_TARGET)], check=False)
            except Exception as e:
                log(f"WARNING: Could not preserve Nextcloud ownership: {e}")
                log("Setting Nextcloud files to www-data:www-data (33:33)")
                subprocess.run(['chown', '-R', '33:33', str(NEXTCLOUD_FILES_TARGET)], check=False)
        
        log("Full restore completed successfully")
        update_restore_status('full', snapshot_path)
        return True
        
    except Exception as e:
        log(f"ERROR: Full restore failed: {e}")
        raise
    finally:
        # Restart containers (full restart)
        log("Restarting containers...")
        for container in stopped:
            restart_container(container)
        
        # Wait for containers to initialize
        import time
        log("Waiting for containers to initialize...")
        time.sleep(10)  # Increased wait time for database to be ready
        
        # Post-restore maintenance
        if 'nextcloud' in apps:
            try:
                result = subprocess.run(
                    ['docker', 'ps', '--format', '{{.Names}}'],
                    capture_output=True,
                    text=True,
                    check=True
                )
                if 'nextcloud' in result.stdout:
                    log("Running Nextcloud maintenance repair...")
                    repair_result = subprocess.run(
                        ['docker', 'exec', '-u', 'www-data', 'nextcloud', 'php', 'occ', 'maintenance:repair'],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60
                    )
                    if repair_result.returncode == 0:
                        log("Nextcloud repair completed successfully")
                    else:
                        log(f"WARNING: Nextcloud repair had issues: {repair_result.stderr[:200]}")
                    
                    log("Triggering Nextcloud file scan...")
                    scan_result = subprocess.run(
                        ['docker', 'exec', '-u', 'www-data', 'nextcloud', 'php', 'occ', 'files:scan', '--all'],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=300
                    )
                    if scan_result.returncode == 0:
                        log("Nextcloud file scan completed successfully")
                    else:
                        log(f"WARNING: Nextcloud scan had issues: {scan_result.stderr[:200]}")
                    
                    log("Scanning Nextcloud app data...")
                    appdata_result = subprocess.run(
                        ['docker', 'exec', '-u', 'www-data', 'nextcloud', 'php', 'occ', 'files:scan-app-data'],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60
                    )
                    if appdata_result.returncode == 0:
                        log("Nextcloud app-data scan completed successfully")
            except Exception as e:
                log(f"WARNING: Could not trigger Nextcloud operations: {e}")
        
        if 'immich' in apps:
            log("NOTE: Immich database was restored, files should appear automatically")
            log("If files don't appear, wait 5-10 minutes for automatic background scan")
            log("Or manually trigger: Web UI → Settings → Library → 'Scan Library'")


def main():
    """Main restore entry point - Full restore only (files + database)."""
    parser = argparse.ArgumentParser(
        description='chronovault Restore - Full restore only (files + database)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /mnt/backup/chronovault/snapshots/daily/2026-01-16
  %(prog)s /mnt/backup/chronovault/snapshots/weekly/2026-03
  %(prog)s /mnt/backup/chronovault/snapshots/daily/2026-01-16 --apps immich
  %(prog)s /mnt/backup/chronovault/snapshots/daily/2026-01-16 --apps nextcloud

Note: Full restore ensures files and database stay in sync.
      This prevents issues with file-only or database-only restores.
        """
    )
    parser.add_argument('snapshot_path', help='Path to snapshot (daily or weekly)')
    parser.add_argument('--apps', nargs='+', choices=['immich', 'nextcloud'], 
                       help='Apps to restore (default: both)')
    parser.add_argument('--verify-backup', action='store_true', default=True,
                       help='Verify BACKUP is mounted and identity (default: True)')
    
    args = parser.parse_args()
    
    try:
        # Verify BACKUP is mounted
        if args.verify_backup:
            if not verify_backup_mounted():
                log("ERROR: BACKUP is not mounted")
                return 1
            
            if not verify_backup_identity():
                log("ERROR: BACKUP identity file missing")
                return 1
        
        # Perform full restore (only option)
        restore_full(args.snapshot_path, args.apps)
        
        log("Restore completed successfully")
        return 0
        
    except Exception as e:
        log(f"ERROR: Restore failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
