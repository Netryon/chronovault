"""Step 16: Run Initial Backup"""

import os
from installer.base import BaseStep


class Step16InitialBackup(BaseStep):
    """Step 16: Run Initial Backup to Populate UI Data"""
    
    @property
    def step_number(self) -> int:
        return 16
    
    @property
    def step_name(self) -> str:
        return "Run Initial Backup"
    
    def execute(self) -> bool:
        """Run initial backup to create status files for the UI"""
        
        backup_script = '/opt/chronovault/scripts/chronovault-backup-run'
        
        if not os.path.exists(backup_script):
            self.log.error(f"Backup script not found: {backup_script}")
            self.log.error("Please ensure Step 15 completed successfully")
            return False
        
        self.log.info("Running initial backup to populate UI status files...")
        self.log.info("This may take a few minutes depending on data size...")
        
        # Run the backup script
        # Note: The script should already be executable and owned by root
        returncode, stdout, stderr = self.runner.run(
            [backup_script],
            timeout=1800  # 30 minutes timeout for initial backup
        )
        
        if returncode != 0:
            self.log.error(f"Backup script failed with return code {returncode}")
            if stderr:
                self.log.error(f"Error output: {stderr}")
            if stdout:
                self.log.info(f"Output: {stdout}")
            return False
        
        # Check if status files were created
        status_file = '/var/lib/chronovault/status.json'
        restore_points_file = '/var/lib/chronovault/restore_points.json'
        
        if os.path.exists(status_file):
            self.log.success("Status file created successfully")
        else:
            self.log.warning("Status file not found after backup - UI may not have data")
        
        if os.path.exists(restore_points_file):
            self.log.success("Restore points file created successfully")
        else:
            self.log.warning("Restore points file not found after backup - UI may not show restore points")
        
        if stdout:
            # Show last few lines of output
            lines = stdout.strip().split('\n')
            if len(lines) > 5:
                self.log.info("Backup output (last 5 lines):")
                for line in lines[-5:]:
                    self.log.info(f"  {line}")
            else:
                self.log.info("Backup output:")
                for line in lines:
                    self.log.info(f"  {line}")
        
        self.log.success("Initial backup completed successfully!")
        self.log.info("The UI should now have status and restore point data available")
        
        return True
