"""Step 18: Setup Backup Timer, System Updates, and Watchtower"""

import os
import shutil
from installer.base import BaseStep


class Step18TimersWatchtower(BaseStep):
    """Step 18: Setup Backup Timer, System Updates, and Watchtower"""
    
    @property
    def step_number(self) -> int:
        return 18
    
    @property
    def step_name(self) -> str:
        return "Setup Backup Timer, System Updates, and Watchtower"
    
    def execute(self) -> bool:
        """Setup backup timer, system update timer, and Watchtower"""
        
        # 1. Setup backup timer
        self.log.info("Setting up backup timer...")
        installer_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        installer_scripts_dir = os.path.join(installer_dir, 'scripts')
        
        backup_files = [
            'chronovault-backup.service',
            'chronovault-backup.timer'
        ]
        
        for backup_file in backup_files:
            src = os.path.join(installer_scripts_dir, backup_file)
            dst = f'/etc/systemd/system/{backup_file}'
            
            if not os.path.exists(src):
                self.log.error(f"{backup_file} not found: {src}")
                return False
            
            shutil.copy2(src, dst)
            self.runner.run(['chmod', '644', dst], timeout=10)
            self.log.info(f"Copied {backup_file}")
        
        # Enable and start backup timer
        self.log.info("Enabling backup timer...")
        returncode, _, stderr = self.runner.run(['systemctl', 'enable', 'chronovault-backup.timer'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to enable backup timer: {stderr}")
            return False
        
        self.log.info("Starting backup timer...")
        returncode, _, stderr = self.runner.run(['systemctl', 'start', 'chronovault-backup.timer'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to start backup timer: {stderr}")
            return False
        
        self.log.success("Backup timer enabled and started")
        
        # 2. Copy system update script
        self.log.info("Copying system update script...")
        update_script_src = os.path.join(installer_scripts_dir, 'chronovault-system-update.sh')
        update_script_dst = '/opt/chronovault/scripts/chronovault-system-update.sh'
        
        if not os.path.exists(update_script_src):
            self.log.error(f"chronovault-system-update.sh not found: {update_script_src}")
            return False
        
        shutil.copy2(update_script_src, update_script_dst)
        self.runner.run(['chmod', '+x', update_script_dst], timeout=10)
        self.log.success("System update script copied and made executable")
        
        # 3. Setup system update timer
        self.log.info("Setting up system update timer...")
        update_files = [
            'chronovault-system-update.service',
            'chronovault-system-update.timer'
        ]
        
        for update_file in update_files:
            src = os.path.join(installer_scripts_dir, update_file)
            dst = f'/etc/systemd/system/{update_file}'
            
            if not os.path.exists(src):
                self.log.error(f"{update_file} not found: {src}")
                return False
            
            shutil.copy2(src, dst)
            self.runner.run(['chmod', '644', dst], timeout=10)
            self.log.info(f"Copied {update_file}")
        
        # Reload systemd
        self.log.info("Reloading systemd daemon...")
        returncode, _, stderr = self.runner.run(['systemctl', 'daemon-reload'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to reload systemd: {stderr}")
            return False
        
        # Enable and start system update timer
        self.log.info("Enabling system update timer...")
        returncode, _, stderr = self.runner.run(['systemctl', 'enable', 'chronovault-system-update.timer'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to enable system update timer: {stderr}")
            return False
        
        self.log.info("Starting system update timer...")
        returncode, _, stderr = self.runner.run(['systemctl', 'start', 'chronovault-system-update.timer'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to start system update timer: {stderr}")
            return False
        
        self.log.success("System update timer enabled and started")
        
        # 4. Setup Watchtower
        self.log.info("Setting up Watchtower for container updates...")
        
        # Get latest versions
        watchtower_version = self.version_checker.get_latest_watchtower_version()
        docker_api_version = self.version_checker.get_latest_docker_api_version()
        
        # Create watchtower.yml
        watchtower_compose = f"""services:
  watchtower:
    image: containrrr/watchtower:{watchtower_version}
    container_name: watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - WATCHTOWER_CLEANUP=true
      - WATCHTOWER_SCHEDULE=0 5 * * *
      - WATCHTOWER_INCLUDE_STOPPED=false
      - WATCHTOWER_REMOVE_VOLUMES=false
      - WATCHTOWER_TIMEOUT=300
      - WATCHTOWER_LIFECYCLE_HOOKS=true
      - WATCHTOWER_ROLLING_RESTART=false
      - DOCKER_API_VERSION={docker_api_version}
    # Optional: Add email notifications if you want
    # environment:
    #   - WATCHTOWER_NOTIFICATIONS=email
    #   - WATCHTOWER_NOTIFICATION_EMAIL_FROM=your-email@example.com
    #   - WATCHTOWER_NOTIFICATION_EMAIL_TO=your-email@example.com
    #   - WATCHTOWER_NOTIFICATION_EMAIL_SERVER=smtp.gmail.com
    #   - WATCHTOWER_NOTIFICATION_EMAIL_SERVER_PORT=587
"""
        
        # Create directory for Watchtower compose file
        watchtower_dir = '/opt/chronovault/compose/watchtower'
        os.makedirs(watchtower_dir, exist_ok=True)
        
        watchtower_yml_path = f'{watchtower_dir}/docker-compose.yml'
        with open(watchtower_yml_path, 'w') as f:
            f.write(watchtower_compose)
        
        self.runner.run(['chown', 'root:root', watchtower_yml_path], timeout=10)
        self.runner.run(['chmod', '644', watchtower_yml_path], timeout=10)
        self.log.success(f"Created watchtower docker-compose.yml with version {watchtower_version} and Docker API {docker_api_version}")
        
        # Start Watchtower
        self.log.info("Starting Watchtower container...")
        returncode, stdout, stderr = self.runner.run(
            ['docker', 'compose', 'up', '-d'],
            cwd=watchtower_dir,
            timeout=120
        )
        
        if returncode != 0:
            self.log.error(f"Failed to start Watchtower: {stderr}")
            return False
        
        # Verify Watchtower is running
        import time
        time.sleep(3)
        returncode, stdout, _ = self.runner.run(['docker', 'ps'], timeout=10)
        if 'watchtower' in stdout:
            self.log.success("Watchtower container started successfully")
        else:
            self.log.warning("Watchtower container may not be running - check with 'docker ps -a'")
        
        self.log.success("All timers and Watchtower setup completed!")
        self.log.info("Backup timer: Daily at 2:00 AM")
        self.log.info("System update timer: Weekly on Sunday at 4:00 AM")
        self.log.info("Watchtower: Daily at 5:00 AM")
        
        return True
