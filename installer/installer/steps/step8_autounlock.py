"""Step 8: Setup Auto-Unlock on Boot (CRITICAL FIX)"""

import os
import tempfile
from installer.base import BaseStep


class Step8SetupAutoUnlock(BaseStep):
    """Step 8: Setup Auto-Unlock on Boot - Fixed with proper subprocess handling"""
    
    @property
    def step_number(self) -> int:
        return 8
    
    @property
    def step_name(self) -> str:
        return "Setup Auto-Unlock on Boot"
    
    def execute(self) -> bool:
        """Setup auto-unlock on boot with proper subprocess handling"""
        primary_disk = self.config.get('PRIMARY_DISK')
        backup_disk = self.config.get('BACKUP_DISK')
        
        if not primary_disk or not backup_disk:
            self.log.error("Primary or Backup disk not set. Please run step 6 first.")
            return False
        
        # Create key files directory
        os.makedirs('/etc/cryptsetup-keys.d', exist_ok=True)
        self.runner.run(['chmod', '700', '/etc/cryptsetup-keys.d'], timeout=10)
        
        # Generate key files
        self.log.info("Generating key files...")
        self.runner.run(
            ['dd', 'if=/dev/urandom', 'of=/etc/cryptsetup-keys.d/crypt-primary.key', 
             'bs=4096', 'count=1'],
            timeout=10
        )
        self.runner.run(['chmod', '400', '/etc/cryptsetup-keys.d/crypt-primary.key'], timeout=10)
        
        self.runner.run(
            ['dd', 'if=/dev/urandom', 'of=/etc/cryptsetup-keys.d/crypt-backup.key',
             'bs=4096', 'count=1'],
            timeout=10
        )
        self.runner.run(['chmod', '400', '/etc/cryptsetup-keys.d/crypt-backup.key'], timeout=10)
        
        # Get LUKS passwords
        self.log.info("Step 8 requires the LUKS passphrases you entered in step 7.")
        self.log.info("For security, we'll ask you to re-enter them now.")
        print()
        
        primary_password = self.prompt.prompt(
            f"Enter LUKS passphrase for PRIMARY disk ({primary_disk})",
            is_password=True
        )
        self.config['LUKS_PASSWORD'] = primary_password
        
        print()
        backup_password = self.prompt.prompt(
            f"Enter LUKS passphrase for BACKUP disk ({backup_disk})",
            is_password=True
        )
        self.config['BACKUP_LUKS_PASSWORD'] = backup_password
        
        # Add keys to LUKS - THIS IS THE CRITICAL FIX
        if not self._add_key_to_luks(primary_disk, primary_password, 
                                     '/etc/cryptsetup-keys.d/crypt-primary.key', "PRIMARY"):
            return False
        
        if not self._add_key_to_luks(backup_disk, backup_password,
                                    '/etc/cryptsetup-keys.d/crypt-backup.key', "BACKUP"):
            return False
        
        # Get UUIDs
        primary_uuid = self.installer.disk_utils.get_disk_uuid(primary_disk)
        backup_uuid = self.installer.disk_utils.get_disk_uuid(backup_disk)
        
        if not primary_uuid or not backup_uuid:
            self.log.error("Failed to get disk UUIDs")
            return False
        
        self.config['PRIMARY_LUKS_UUID'] = primary_uuid
        self.config['BACKUP_LUKS_UUID'] = backup_uuid
        
        # Create storage config
        os.makedirs('/etc/chronovault', exist_ok=True)
        with open('/etc/chronovault/storage.conf', 'w') as f:
            f.write(f"PRIMARY_LUKS_UUID={primary_uuid}\n")
            f.write(f"BACKUP_LUKS_UUID={backup_uuid}\n")
        
        self.runner.run(['chmod', '640', '/etc/chronovault/storage.conf'], timeout=10)
        
        # Create systemd services
        self._create_systemd_services()
        
        # Disable crypttab if it exists
        if os.path.exists('/etc/crypttab'):
            os.rename('/etc/crypttab', '/etc/crypttab.disabled')
        
        # Enable primary service only
        self.runner.run(['systemctl', 'daemon-reload'], timeout=30)
        self.runner.run(['systemctl', 'enable', 'crypt-primary.service'], timeout=30)
        self.runner.run(['systemctl', 'disable', 'crypt-backup.service'], timeout=30)
        
        self.log.success("Step 8 completed - reboot required")
        self.log.warning("System will reboot to test auto-unlock...")
        
        if self.prompt.prompt_yesno("Reboot now to test auto-unlock?", default="yes"):
            self.runner.run(['reboot'], timeout=10)
        
        return True
    
    def _add_key_to_luks(self, disk: str, password: str, key_file: str, label: str) -> bool:
        """
        Add key file to LUKS device - CRITICAL FIX using proper subprocess handling
        
        This fixes the hanging issue by:
        1. Using subprocess.run() with explicit timeout
        2. Proper stdin handling (DEVNULL to prevent hanging)
        3. Better error detection from return codes
        """
        self.log.info(f"Adding key file to {label} disk ({disk})...")
        self.log.info("This operation may take 10-30 seconds, please wait...")
        
        # Create temporary passphrase file
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp_file:
            tmp_file.write(password)
            tmp_pass_file = tmp_file.name
        
        try:
            os.chmod(tmp_pass_file, 0o600)
            
            # CRITICAL FIX: Use subprocess.run() with timeout and proper stdin handling
            # This prevents the hanging issue that occurred in bash
            returncode, stdout, stderr = self.runner.run(
                ['cryptsetup', 'luksAddKey', disk, key_file, '--key-file', tmp_pass_file, '-q'],
                timeout=60,
                stdin_devnull=True  # Redirect stdin to /dev/null to prevent hanging
            )
            
            if returncode != 0:
                error_msg = stderr.lower()
                if any(keyword in error_msg for keyword in ['no key available', 'wrong', 'invalid', 'bad']):
                    self.log.error(f"The passphrase for {label} disk ({disk}) appears to be incorrect.")
                    self.log.warning("Please verify you're using the correct passphrase from step 7.")
                    print()
                    
                    # Retry with new password
                    new_password = self.prompt.prompt(
                        f"Re-enter LUKS passphrase for {label} disk ({disk})",
                        is_password=True
                    )
                    
                    # Update temp file and try again
                    with open(tmp_pass_file, 'w') as f:
                        f.write(new_password)
                    
                    self.log.info("Retrying with new passphrase...")
                    returncode, stdout, stderr = self.runner.run(
                        ['cryptsetup', 'luksAddKey', disk, key_file, '--key-file', tmp_pass_file, '-q'],
                        timeout=60,
                        stdin_devnull=True
                    )
                    
                    if returncode != 0:
                        self.log.error("Still failed after re-entering passphrase.")
                        self.log.error(f"Please verify the {label} disk was encrypted correctly in step 7.")
                        self.log.error(f"Error: {stderr}")
                        return False
                else:
                    self.log.error(f"Failed to add key to {label} disk ({disk}).")
                    self.log.error(f"Error: {stderr}")
                    return False
            
            self.log.success(f"Key successfully added to {label} disk")
            return True
        
        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_pass_file)
            except Exception:
                pass
    
    def _create_systemd_services(self):
        """Create systemd service files for auto-unlock"""
        # Primary service
        primary_service = """[Unit]
Description=Unlock primary encrypted storage
DefaultDependencies=no
After=local-fs-pre.target systemd-udev-settle.service
Wants=systemd-udev-settle.service
Before=local-fs.target
[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/chronovault/storage.conf
ExecStart=/sbin/cryptsetup open /dev/disk/by-uuid/${PRIMARY_LUKS_UUID} crypt-primary --key-file /etc/cryptsetup-keys.d/crypt-primary.key
ExecStart=/bin/mkdir -p /mnt/primary
ExecStart=/bin/mount /dev/mapper/crypt-primary /mnt/primary
[Install]
WantedBy=multi-user.target
"""
        
        with open('/etc/systemd/system/crypt-primary.service', 'w') as f:
            f.write(primary_service)
        
        # Backup service (on-demand only)
        backup_service = """[Unit]
Description=Unlock backup encrypted storage (on-demand only)
DefaultDependencies=no
After=local-fs-pre.target systemd-udev-settle.service crypt-primary.service
Wants=systemd-udev-settle.service
Before=local-fs.target
[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile=/etc/chronovault/storage.conf
ExecStart=/sbin/cryptsetup open /dev/disk/by-uuid/${BACKUP_LUKS_UUID} crypt-backup --key-file /etc/cryptsetup-keys.d/crypt-backup.key
ExecStart=/bin/mkdir -p /mnt/backup
ExecStart=/bin/mount /dev/mapper/crypt-backup /mnt/backup
[Install]
WantedBy=multi-user.target
"""
        
        with open('/etc/systemd/system/crypt-backup.service', 'w') as f:
            f.write(backup_service)
        
        # Create mount/unmount scripts for backup disk
        self._create_backup_scripts()
    
    def _create_backup_scripts(self):
        """Create mount/unmount helper scripts for backup disk"""
        # Ensure scripts directory exists
        os.makedirs('/opt/chronovault/scripts', exist_ok=True)
        
        # Mount script
        mount_script = """#!/bin/bash
set -euo pipefail

. /etc/chronovault/storage.conf

IDENTITY_FILE="/mnt/backup/chronovault/metadata/IDENTITY"
METADATA_DIR="/mnt/backup/chronovault/metadata"
CHRONOVAULT_DIR="/mnt/backup/chronovault"

# Helper function to force filesystem to initialize by accessing parent directories
# This "wakes up" the filesystem and makes metadata available
force_fs_init() {
  # Access parent directories in order to force filesystem initialization
  ls /mnt/backup >/dev/null 2>&1 || true
  ls "${CHRONOVAULT_DIR}" >/dev/null 2>&1 || true
  ls "${METADATA_DIR}" >/dev/null 2>&1 || true
  # Also try stat on parent to force metadata read
  stat /mnt/backup >/dev/null 2>&1 || true
  stat "${CHRONOVAULT_DIR}" >/dev/null 2>&1 || true
  sync
}

# Helper function to check if directory exists (with retry capability)
# Uses multiple methods to verify directory existence
check_directory_exists() {
  # Try multiple checks to ensure directory is really accessible
  [ -d "${METADATA_DIR}" ] && [ -e "${METADATA_DIR}" ] && test -d "${METADATA_DIR}"
}

# Helper function to check if IDENTITY file exists (using stat to force filesystem read)
# This forces the filesystem to actually read metadata, not just check cache
check_identity_exists() {
  stat "${IDENTITY_FILE}" >/dev/null 2>&1
}

# If already mounted, verify it's the correct device and has IDENTITY; otherwise unmount and remount
if mountpoint -q /mnt/backup; then
  CURRENT_DEV="$(findmnt -n -o SOURCE /mnt/backup || echo "")"

  # If it's already /dev/mapper/crypt-backup and metadata/IDENTITY are present, we can trust it
  if [ "${CURRENT_DEV}" = "/dev/mapper/crypt-backup" ] && check_directory_exists && check_identity_exists; then
    echo "OK: BACKUP already mounted"
    exit 0
  fi

  # Otherwise this is either the wrong device (e.g. rootfs) or missing layout.
  # Unmount and continue with the new-mount path below.
  if ! /bin/umount /mnt/backup; then
    echo "ERROR: BACKUP mounted on incorrect device (${CURRENT_DEV}) and could not be unmounted"
    exit 1
  fi
fi

# If mapper already open (previous partial run), don't fail
if ! lsblk -nr -o NAME | grep -qx "crypt-backup"; then
  /sbin/cryptsetup open "/dev/disk/by-uuid/${BACKUP_LUKS_UUID}" crypt-backup \\
    --key-file /etc/cryptsetup-keys.d/crypt-backup.key
fi

/bin/mkdir -p /mnt/backup
/bin/mount /dev/mapper/crypt-backup /mnt/backup

mountpoint -q /mnt/backup

# Sync to ensure mount is fully ready
sync

# Force filesystem to initialize immediately
force_fs_init

# Wait for filesystem to be ready and verify IDENTITY with retries
# Try immediately first
if check_directory_exists && check_identity_exists; then
  echo "OK: BACKUP mounted"
  exit 0
fi

# Retry with increasing delays: 0.2s, 0.5s, 1s, 2s, 3s, 5s
# Longer delays to give filesystem more time to initialize
for delay in 0.2 0.5 1 2 3 5; do
  sleep ${delay}
  force_fs_init
  # Check directory first, then file
  if check_directory_exists && check_identity_exists; then
    echo "OK: BACKUP mounted"
    exit 0
  fi
done

# Final aggressive retry with even longer delays (filesystem might need significant time)
for delay in 2 3 5; do
  sleep ${delay}
  force_fs_init
  if check_directory_exists; then
    # Directory exists, check file one more time
    if check_identity_exists; then
      echo "OK: BACKUP mounted"
      exit 0
    else
      echo "ERROR: BACKUP mounted but ${IDENTITY_FILE} missing (wrong disk/layout?)"
      exit 1
    fi
  fi
done

# If we get here, directory is still missing after all retries (total ~20 seconds)
echo "ERROR: BACKUP mounted but directory ${METADATA_DIR} does not exist (wrong disk/layout?)"
exit 1
"""
        
        mount_script_path = '/opt/chronovault/scripts/chronovault-backup-mount'
        with open(mount_script_path, 'w') as f:
            f.write(mount_script)
        os.chmod(mount_script_path, 0o700)
        self.runner.run(['chown', 'root:root', mount_script_path], timeout=10)
        
        # Unmount script
        umount_script = """#!/bin/bash
set -euo pipefail

# Unmount if mounted
if mountpoint -q /mnt/backup; then
  /bin/umount /mnt/backup
fi

# Close if open
if lsblk -nr -o NAME | grep -qx "crypt-backup"; then
  /sbin/cryptsetup close crypt-backup
fi

# Verify closed
if mountpoint -q /mnt/backup; then
  echo "ERROR: /mnt/backup still mounted"
  exit 1
fi
if lsblk -nr -o NAME | grep -qx "crypt-backup"; then
  echo "ERROR: crypt-backup mapper still present"
  exit 1
fi

echo "OK: BACKUP unmounted + closed"
"""
        
        umount_script_path = '/opt/chronovault/scripts/chronovault-backup-umount'
        with open(umount_script_path, 'w') as f:
            f.write(umount_script)
        os.chmod(umount_script_path, 0o700)
        self.runner.run(['chown', 'root:root', umount_script_path], timeout=10)
        
        self.log.success("Created backup mount/unmount scripts")
