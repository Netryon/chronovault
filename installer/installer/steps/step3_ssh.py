"""Step 3: Configure SSH"""

import os
from installer.base import BaseStep


class Step3ConfigureSSH(BaseStep):
    """Step 3: Configure SSH"""
    
    @property
    def step_number(self) -> int:
        return 3
    
    @property
    def step_name(self) -> str:
        return "Configure SSH"
    
    def execute(self) -> bool:
        """Configure SSH hardening"""
        if not self.prompt.prompt_yesno("Configure SSH hardening?", default="yes"):
            return True
        
        # Create SSH config directory
        os.makedirs('/etc/ssh/sshd_config.d', exist_ok=True)
        
        # Create SSH hardening config
        ssh_config = """# Chronovault SSH hardening (basic)
PermitRootLogin no
PasswordAuthentication yes
PubkeyAuthentication yes
MaxAuthTries 5
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
"""
        
        with open('/etc/ssh/sshd_config.d/10-chronovault.conf', 'w') as f:
            f.write(ssh_config)
        
        # Validate and restart SSH
        self.runner.run(['sshd', '-t'], timeout=10)
        self.runner.run(['systemctl', 'restart', 'ssh'], timeout=30)
        
        self.log.success("SSH hardening configured")
        return True
