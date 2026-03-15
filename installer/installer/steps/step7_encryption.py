"""Step 7: Setup Encrypted Storage"""

import os
import stat
import tempfile
from installer.base import BaseStep


class Step7SetupEncryption(BaseStep):
    """Step 7: Setup Encrypted Storage"""
    
    @property
    def step_number(self) -> int:
        return 7
    
    @property
    def step_name(self) -> str:
        return "Setup Encrypted Storage"
    
    def execute(self) -> bool:
        """Setup encrypted storage on primary and backup disks"""
        primary_disk = self.config.get('PRIMARY_DISK')
        backup_disk = self.config.get('BACKUP_DISK')
        
        if not primary_disk or not backup_disk:
            self.log.error("Primary or Backup disk not set. Please run step 6 first.")
            return False
        
        # Validate disks exist and are block devices
        if not os.path.exists(primary_disk) or not os.path.exists(backup_disk):
            self.log.error(f"Selected disks ({primary_disk}, {backup_disk}) do not exist.")
            return False
        
        try:
            if not stat.S_ISBLK(os.stat(primary_disk).st_mode) or not stat.S_ISBLK(os.stat(backup_disk).st_mode):
                self.log.error(f"Selected disks ({primary_disk}, {backup_disk}) are not block devices.")
                return False
        except (OSError, FileNotFoundError) as e:
            self.log.error(f"Error validating disks: {e}")
            return False
        
        self.log.info("Using disks from step 6:")
        self.log.info(f"  Primary: {primary_disk}")
        self.log.info(f"  Backup: {backup_disk}")
        print()
        
        # Get LUKS passwords
        primary_password = self.config.get('LUKS_PASSWORD')
        if not primary_password:
            primary_password = self.prompt.prompt(
                f"Enter LUKS encryption password for PRIMARY disk ({primary_disk})",
                is_password=True
            )
            self.config['LUKS_PASSWORD'] = primary_password
        
        backup_password = self.config.get('BACKUP_LUKS_PASSWORD')
        if not backup_password:
            backup_password = self.prompt.prompt(
                f"Enter LUKS encryption password for BACKUP disk ({backup_disk})",
                is_password=True
            )
            self.config['BACKUP_LUKS_PASSWORD'] = backup_password
        
        # Setup PRIMARY disk
        if not self._setup_disk(primary_disk, primary_password, "PRIMARY", "crypt-primary", "/mnt/primary"):
            return False
        
        # Setup BACKUP disk
        print()
        if not self._setup_disk(backup_disk, backup_password, "BACKUP", "crypt-backup", "/mnt/backup", is_backup=True):
            return False
        
        return True
    
    def _setup_disk(self, disk: str, password: str, label: str, mapper_name: str, 
                    mount_point: str, is_backup: bool = False) -> bool:
        """Setup encryption on a disk"""
        self.log.info(f"Setting up {label} disk: {disk}")
        self.log.warning(f"This will ERASE all data on {disk}!")
        
        if not self.prompt.prompt_yesno(f"Continue with {label} disk setup?", default="no"):
            self.log.error(f"Aborted {label} disk setup")
            return False
        
        # Wipe filesystem signatures
        self.log.info(f"Wiping filesystem signatures from {disk}...")
        try:
            self.runner.run(['wipefs', '-a', disk], timeout=30)
        except Exception:
            # Ignore errors if already clean
            pass
        
        # Create LUKS encryption using key file (more secure and reliable)
        self.log.info(f"Creating LUKS encryption on {disk}...")
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as key_file:
            key_file.write(password)
            key_file_path = key_file.name
        
        try:
            # Set restrictive permissions on key file
            os.chmod(key_file_path, 0o600)
            
            # Use --key-file for luksFormat
            self.runner.run(
                ['cryptsetup', 'luksFormat', disk, '--key-file', key_file_path, '-q'],
                timeout=60,
                stdin_devnull=True
            )
            
            # Open encrypted volume using key file
            self.log.info("Opening encrypted volume...")
            self.runner.run(
                ['cryptsetup', 'open', disk, mapper_name, '--key-file', key_file_path],
                timeout=30,
                stdin_devnull=True
            )
        finally:
            # Securely remove the temporary key file
            try:
                os.remove(key_file_path)
            except Exception:
                pass
        
        # Create ext4 filesystem
        self.log.info("Creating ext4 filesystem...")
        self.runner.run(
            ['mkfs.ext4', '-F', '-L', label, f'/dev/mapper/{mapper_name}'],
            timeout=60,
            capture_output=False
        )
        
        # Mount
        self.log.info(f"Mounting {label} disk...")
        os.makedirs(mount_point, exist_ok=True)
        self.runner.run(['mount', f'/dev/mapper/{mapper_name}', mount_point], timeout=30)
        
        if is_backup:
            # Create backup folder structure
            self.log.info("Creating backup folder structure...")
            backup_dirs = [
                '/mnt/backup/chronovault/current',
                '/mnt/backup/chronovault/snapshots/daily',
                '/mnt/backup/chronovault/snapshots/weekly',
                '/mnt/backup/chronovault/databases/immich',
                '/mnt/backup/chronovault/databases/nextcloud',
                '/mnt/backup/chronovault/logs',
                '/mnt/backup/chronovault/metadata'
            ]
            for dir_path in backup_dirs:
                os.makedirs(dir_path, exist_ok=True)
            
            with open('/mnt/backup/chronovault/metadata/IDENTITY', 'w') as f:
                f.write('CHRONOVAULT_BACKUP_DISK')
            
            self.runner.run(['chown', '-R', 'root:root', '/mnt/backup/chronovault'], timeout=30)
            self.runner.run(['chmod', '-R', '750', '/mnt/backup/chronovault'], timeout=30)
            
            # Unmount backup (cold storage)
            self.log.info("Unmounting BACKUP disk and closing LUKS device (cold storage)...")
            self.runner.run(['umount', mount_point], timeout=30)
            self.runner.run(['cryptsetup', 'close', mapper_name], timeout=30)
            self.log.success(f"{label} disk encrypted and closed (cold storage)")
        else:
            self.log.success(f"{label} disk encrypted and mounted at {mount_point}")
        
        return True
