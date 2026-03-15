"""Step 10: Install Docker Engine + Compose"""

from installer.base import BaseStep


class Step10InstallDocker(BaseStep):
    """Step 10: Install Docker Engine + Compose"""
    
    @property
    def step_number(self) -> int:
        return 10
    
    @property
    def step_name(self) -> str:
        return "Install Docker Engine + Compose"
    
    def execute(self) -> bool:
        """Install Docker and Docker Compose"""
        # Check if Docker is already installed
        if self.runner.check_command('docker'):
            self.log.info("Docker already installed")
            # Verify Docker works
            returncode, _, _ = self.runner.run(['docker', 'version'], timeout=10)
            if returncode == 0:
                return True
        
        self.log.info("Installing Docker...")
        
        # Use Docker's official install script (simpler and more reliable)
        returncode, stdout, stderr = self.runner.run(
            ['sh', '-c', 'curl -fsSL https://get.docker.com | sh'],
            timeout=600
        )
        
        if returncode != 0:
            self.log.error(f"Docker installation failed: {stderr}")
            return False
        
        # Add sysadmin user to docker group
        self.runner.run(['usermod', '-aG', 'docker', 'sysadmin'], timeout=10)
        
        # Verify Docker
        returncode, _, _ = self.runner.run(['docker', 'version'], timeout=10)
        if returncode != 0:
            self.log.error("Docker not working after installation!")
            return False
        
        # Test Docker
        returncode, _, _ = self.runner.run(['docker', 'run', '--rm', 'hello-world'], timeout=60)
        if returncode != 0:
            self.log.error("Docker test failed!")
            return False
        
        self.log.success("Docker installed and verified")
        return True
