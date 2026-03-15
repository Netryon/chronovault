"""Step 2: Install Base Packages"""

from installer.base import BaseStep


class Step2InstallPackages(BaseStep):
    """Step 2: Install Base Packages"""
    
    @property
    def step_number(self) -> int:
        return 2
    
    @property
    def step_name(self) -> str:
        return "Install Base Packages"
    
    def execute(self) -> bool:
        """Install base packages"""
        self.log.info("Updating package list...")
        self.runner.run(['apt-get', 'update', '-qq'], timeout=300)
        
        packages = [
            'ca-certificates', 'curl', 'wget', 'gnupg', 'lsb-release', 'jq', 'unzip',
            'rsync', 'cron', 'logrotate', 'htop', 'ncdu', 'tree', 'ufw',
            'cryptsetup', 'cryptsetup-initramfs'
        ]
        
        self.log.info("Installing base packages...")
        self.runner.run(['apt-get', 'install', '-y'] + packages, timeout=600)
        
        # Enable persistent journald storage
        self.log.info("Enabling persistent journald storage...")
        import os
        os.makedirs('/var/log/journal', exist_ok=True)
        self.runner.run(['systemctl', 'restart', 'systemd-journald'], timeout=30)
        
        return True
