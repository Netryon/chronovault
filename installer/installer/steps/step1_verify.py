"""Step 1: Verify Base System"""

import socket
import platform
from installer.base import BaseStep


class Step1VerifySystem(BaseStep):
    """Step 1: Verify Base System"""
    
    @property
    def step_number(self) -> int:
        return 1
    
    @property
    def step_name(self) -> str:
        return "Verify Base System"
    
    def execute(self) -> bool:
        """Verify base system information"""
        self.log.info("System Information:")
        
        # Get hostname
        hostname = socket.gethostname()
        print(f"  Hostname: {hostname}")
        
        # Get architecture
        arch = platform.machine()
        print(f"  Architecture: {arch}")
        
        # Get OS info
        try:
            with open('/etc/os-release', 'r') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        os_name = line.split('=', 1)[1].strip().strip('"')
                        print(f"  OS: {os_name}")
                        break
        except Exception:
            print("  OS: Unknown")
        
        # Get IP address
        try:
            import subprocess
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
            ip = result.stdout.strip().split()[0] if result.stdout.strip() else "Unknown"
            print(f"  IP Address: {ip}")
        except Exception:
            print("  IP Address: Unknown")
        
        # Get timezone
        try:
            result = subprocess.run(['timedatectl'], capture_output=True, text=True, timeout=5)
            for line in result.stdout.split('\n'):
                if 'Time zone' in line:
                    timezone = line.split('Time zone:')[1].strip().split()[0]
                    print(f"  Timezone: {timezone}")
                    break
        except Exception:
            print("  Timezone: Unknown")
        
        return True
