"""Step 4: Configure Firewall (UFW)"""

from installer.base import BaseStep


class Step4ConfigureFirewall(BaseStep):
    """Step 4: Configure Firewall (UFW)"""
    
    @property
    def step_number(self) -> int:
        return 4
    
    @property
    def step_name(self) -> str:
        return "Configure Firewall (UFW)"
    
    def execute(self) -> bool:
        """Configure UFW firewall"""
        # Set default policies
        self.runner.run(['ufw', 'default', 'deny', 'incoming'], timeout=30)
        self.runner.run(['ufw', 'default', 'allow', 'outgoing'], timeout=30)
        
        # Allow SSH
        self.runner.run(['ufw', 'allow', 'ssh'], timeout=30)
        
        # Enable firewall
        self.runner.run(['ufw', '--force', 'enable'], timeout=30)
        
        self.log.success("Firewall configured (SSH allowed)")
        return True
