"""Step 5: Create Chronovault Project Folders"""

import os
from installer.base import BaseStep


class Step5CreateFolders(BaseStep):
    """Step 5: Create Chronovault Project Folders"""
    
    @property
    def step_number(self) -> int:
        return 5
    
    @property
    def step_name(self) -> str:
        return "Create Chronovault Project Folders"
    
    def execute(self) -> bool:
        """Create project folder structure"""
        # Create main directories
        os.makedirs('/opt/chronovault/compose', exist_ok=True)
        os.makedirs('/opt/chronovault/configs', exist_ok=True)
        os.makedirs('/opt/chronovault/env', exist_ok=True)
        os.makedirs('/opt/chronovault/scripts', exist_ok=True)
        os.makedirs('/opt/chronovault/logs', exist_ok=True)
        os.makedirs('/var/log/chronovault', exist_ok=True)
        
        # Set permissions
        self.runner.run(['chown', '-R', 'root:root', '/opt/chronovault'], timeout=30)
        self.runner.run(['chmod', '-R', '755', '/opt/chronovault'], timeout=30)
        self.runner.run(['chown', '-R', 'root:root', '/var/log/chronovault'], timeout=30)
        self.runner.run(['chmod', '755', '/var/log/chronovault'], timeout=30)
        
        return True
