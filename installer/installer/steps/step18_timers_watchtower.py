"""Step 18: Setup Backup Timer, System Updates, and Container Maintenance"""

import os
import shutil
from installer.base import BaseStep


class Step18TimersContainerMaintenance(BaseStep):
    """Step 18: Setup backup timer, system update timer, safe container updater, and stack guard"""
    
    @property
    def step_number(self) -> int:
        return 18
    
    @property
    def step_name(self) -> str:
        return "Setup Backup Timer, System Updates, and Container Maintenance"
    
    def _deploy_script(self, installer_scripts_dir: str, filename: str) -> bool:
        src = os.path.join(installer_scripts_dir, filename)
        dst = f'/opt/chronovault/scripts/{filename}'
        if not os.path.exists(src):
            self.log.error(f"{filename} not found: {src}")
            return False
        shutil.copy2(src, dst)
        if filename.endswith('.sh'):
            self.runner.run(['chmod', '+x', dst], timeout=10)
        else:
            self.runner.run(['chmod', '644', dst], timeout=10)
        self.log.info(f"Copied {filename}")
        return True
    
    def _deploy_systemd_units(self, installer_scripts_dir: str, unit_files: list) -> bool:
        for unit_file in unit_files:
            src = os.path.join(installer_scripts_dir, unit_file)
            dst = f'/etc/systemd/system/{unit_file}'
            if not os.path.exists(src):
                self.log.error(f"{unit_file} not found: {src}")
                return False
            shutil.copy2(src, dst)
            self.runner.run(['chmod', '644', dst], timeout=10)
            self.log.info(f"Copied {unit_file}")
        return True
    
    def _enable_timer(self, timer_name: str) -> bool:
        returncode, _, stderr = self.runner.run(['systemctl', 'enable', timer_name], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to enable {timer_name}: {stderr}")
            return False
        returncode, _, stderr = self.runner.run(['systemctl', 'start', timer_name], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to start {timer_name}: {stderr}")
            return False
        self.log.success(f"{timer_name} enabled and started")
        return True
    
    def execute(self) -> bool:
        """Setup backup timer, system update timer, safe container updater, and stack guard"""
        
        installer_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        installer_scripts_dir = os.path.join(installer_dir, 'scripts')
        
        # 1. Setup backup timer
        self.log.info("Setting up backup timer...")
        if not self._deploy_systemd_units(installer_scripts_dir, [
            'chronovault-backup.service',
            'chronovault-backup.timer',
        ]):
            return False
        
        # 2. System update script + timer
        self.log.info("Setting up system update timer...")
        if not self._deploy_script(installer_scripts_dir, 'chronovault-system-update.sh'):
            return False
        if not self._deploy_systemd_units(installer_scripts_dir, [
            'chronovault-system-update.service',
            'chronovault-system-update.timer',
        ]):
            return False
        
        # 3. Safe container updater + stack guard scripts
        self.log.info("Setting up safe container updater and stack guard...")
        for script in (
            'chronovault-container-update.sh',
            'chronovault-stack-guard.sh',
        ):
            if not self._deploy_script(installer_scripts_dir, script):
                return False
        
        if not self._deploy_systemd_units(installer_scripts_dir, [
            'chronovault-container-update.service',
            'chronovault-container-update.timer',
            'chronovault-stack-guard.service',
            'chronovault-stack-guard.timer',
        ]):
            return False
        
        # Reload systemd once after all unit files are in place
        self.log.info("Reloading systemd daemon...")
        returncode, _, stderr = self.runner.run(['systemctl', 'daemon-reload'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to reload systemd: {stderr}")
            return False
        
        if not self._enable_timer('chronovault-backup.timer'):
            return False
        if not self._enable_timer('chronovault-system-update.timer'):
            return False
        if not self._enable_timer('chronovault-container-update.timer'):
            return False
        if not self._enable_timer('chronovault-stack-guard.timer'):
            return False
        
        # 4. Ensure Watchtower is not running (replaced by safe compose-stack updater)
        watchtower_dir = '/opt/chronovault/compose/watchtower'
        if os.path.isdir(watchtower_dir):
            self.log.info("Stopping legacy Watchtower stack if present...")
            self.runner.run(['docker', 'compose', 'down'], cwd=watchtower_dir, timeout=120)
        
        self.log.success("All timers and container maintenance setup completed!")
        self.log.info("Backup timer: Daily at 2:00 AM (America/New_York)")
        self.log.info("System update timer: Weekly on Sunday at 4:00 AM")
        self.log.info("Safe container updater: Daily at 5:00 AM UTC")
        self.log.info("Stack guard: Every 15 minutes")
        
        return True
