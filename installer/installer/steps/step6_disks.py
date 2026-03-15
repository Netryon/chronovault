"""Step 6: Select Primary and Backup Disks"""

from installer.base import BaseStep


class Step6SelectDisks(BaseStep):
    """Step 6: Select Primary and Backup Disks"""
    
    @property
    def step_number(self) -> int:
        return 6
    
    @property
    def step_name(self) -> str:
        return "Select Primary and Backup Disks"
    
    def execute(self) -> bool:
        """Select primary and backup disks"""
        self.log.warning("⚠️  DESTRUCTIVE OPERATION ⚠️")
        self.log.warning("This will ERASE all data on the selected disks!")
        print()
        
        if not self.prompt.prompt_yesno("Have you backed up any important data on these disks?", default="no"):
            self.log.error("Please backup your data first! Exiting.")
            return False
        
        # Select Primary disk
        self.log.info("Selecting PRIMARY disk...")
        primary_disk = self._select_disk("Primary")
        self.config['PRIMARY_DISK'] = primary_disk
        self.log.success(f"Primary disk selected: {primary_disk}")
        print()
        
        # Select Backup disk (excluding primary)
        self.log.info("Selecting BACKUP disk...")
        backup_disk = self._select_disk("Backup", exclude_disk=primary_disk)
        self.config['BACKUP_DISK'] = backup_disk
        self.log.success(f"Backup disk selected: {backup_disk}")
        
        # Verify they're different
        if primary_disk == backup_disk:
            self.log.error("Primary and Backup disks cannot be the same! Exiting.")
            return False
        
        print()
        self.log.success("Disk selection complete:")
        self.log.success(f"  Primary: {primary_disk}")
        self.log.success(f"  Backup: {backup_disk}")
        
        return True
    
    def _select_disk(self, purpose: str, exclude_disk: str = None) -> str:
        """Select a disk interactively"""
        while True:
            # Show available disks
            disks = self.installer.disk_utils.list_disks(exclude_disk=exclude_disk)
            self.installer.disk_utils.display_disks(disks)
            
            # Prompt for selection
            selected = self.prompt.prompt(
                f"Enter the device name for {purpose} storage (e.g., sda, sdb, nvme0n1)"
            ).strip()
            
            # Remove /dev/ prefix if included
            if selected.startswith('/dev/'):
                selected = selected[5:]
            
            disk_path = f"/dev/{selected}"
            
            # Check if trying to select excluded disk
            if exclude_disk and disk_path == exclude_disk:
                self.log.error("This disk is already selected as Primary. Please choose a different disk.")
                continue
            
            # Validate disk exists
            if not self.installer.disk_utils.validate_disk(disk_path):
                self.log.error(f"Device {disk_path} does not exist. Please try again.")
                continue
            
            # Get disk info for confirmation
            disk_info = None
            for d in disks:
                if d['path'] == disk_path:
                    disk_info = d
                    break
            
            if disk_info:
                print()
                self.log.info(f"You selected: {disk_path}")
                print(f"  Size: {disk_info['size']}")
                print(f"  Model: {disk_info['model']}")
                print(f"  Type: {disk_info['type']}")
                print(f"  Mountpoint: {disk_info['mountpoint']}")
                print()
                
                if disk_info['mountpoint'] != "(not mounted)":
                    self.log.warning(f"⚠️  WARNING: Disk {disk_path} is currently mounted at {disk_info['mountpoint']}")
                
                self.log.warning(f"⚠️  WARNING: This will ERASE ALL DATA on {disk_path}!")
                
                if self.prompt.prompt_yesno(f"Confirm this is the correct {purpose} disk?", default="no"):
                    return disk_path
            
            self.log.error(f"Device {disk_path} does not exist. Please try again.")
