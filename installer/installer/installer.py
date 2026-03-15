"""Main Chronovault Installer class"""

import os
import json
import sys
from typing import Dict, Any, Optional
from datetime import datetime

from .utils.logging import Logger, Colors
from .utils.prompts import Prompter
from .utils.subprocess import SubprocessRunner
from .utils.disks import DiskUtils
from .utils.versions import VersionChecker
from .base import BaseStep


BANNER = """
╔═══════════════════════════════════════════════════════════════════════════════════════════════╗
║   ██████╗██╗  ██╗██████╗  ██████╗ ███╗   ██╗ ██████╗ ██╗   ██╗ █████╗ ██╗   ██╗██╗  ████████╗ ║
║  ██╔════╝██║  ██║██╔══██╗██╔═══██╗████╗  ██║██╔═══██╗██║   ██║██╔══██╗██║   ██║██║  ╚══██╔══╝ ║
║  ██║     ███████║██████╔╝██║   ██║██╔██╗ ██║██║   ██║██║   ██║███████║██║   ██║██║     ██║    ║
║  ██║     ██╔══██║██╔══██╗██║   ██║██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║██║   ██║██║     ██║    ║
║  ╚██████╗██║  ██║██║  ██║╚██████╔╝██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║╚██████╔╝███████╗██║    ║
║   ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝    ║
║                                                                                               ║
║                                                                                               ║
║                         Photo & Document Server - Automated Installer                         ║
║                                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════╝"""


class ChronovaultInstaller:
    """Main installer class"""
    
    def __init__(self, state_file: str = "/root/.chronovault-installer-state.json",
                 log_file: str = "/tmp/chronovault-installer.log"):
        self.state_file = state_file
        self.log_file = log_file
        self.config: Dict[str, Any] = {}
        self.steps: list[BaseStep] = []
        
        # Initialize utilities
        self.log = Logger(log_file)
        self.prompt = Prompter(self.log)
        self.runner = SubprocessRunner(self.log)
        self.disk_utils = DiskUtils(self.log)
        self.version_checker = VersionChecker(self.log)
        
        # Initialize state
        self._init_state()
        self._load_state()
    
    def _init_state(self):
        """Initialize state file if it doesn't exist"""
        if not os.path.exists(self.state_file):
            state = {
                "current_step": 0,
                "completed_steps": [],
                "config": {},
                "start_time": "",
                "last_update": ""
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
            self.log.info(f"Initialized state file: {self.state_file}")
    
    def _load_state(self) -> int:
        """Load state from file and return current step number"""
        if not os.path.exists(self.state_file):
            self.log.info("Loaded state: current step is 0")
            return 0
        
        try:
            # Check if file is empty
            if os.path.getsize(self.state_file) == 0:
                self.log.warning("State file is empty, starting fresh")
                # Clean up empty file
                try:
                    os.remove(self.state_file)
                except Exception:
                    pass
                return 0
            
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            
            current_step = state.get('current_step', 0)
            self.log.info(f"Loaded state: current step is {current_step}")
            
            # Load config
            self.config = state.get('config', {})
            
            return current_step
        except (json.JSONDecodeError, IOError, ValueError) as e:
            self.log.warning(f"Error loading state: {e}. Starting fresh.")
            # Clean up corrupted file
            try:
                os.remove(self.state_file)
                self.log.info("Removed corrupted state file")
            except Exception:
                pass
            return 0
    
    def check_root(self):
        """Check if running as root"""
        if os.geteuid() != 0:
            self.log.error("This script must be run as root (use sudo)")
            sys.exit(1)
    
    def check_dependencies(self):
        """Check and install required dependencies"""
        missing = []
        
        # Check jq - check both command availability and package installation
        jq_installed = False
        if self.runner.check_command('jq'):
            jq_installed = True
        else:
            # Check if package is installed via dpkg
            returncode, stdout, _ = self.runner.run(['dpkg', '-l', 'jq'], timeout=5)
            if returncode == 0 and 'jq' in stdout and 'ii' in stdout:  # 'ii' means installed
                jq_installed = True
        
        if not jq_installed:
            missing.append('jq')
        
        # Check other dependencies
        for cmd in ['curl', 'wget']:
            if not self.runner.check_command(cmd):
                missing.append(cmd)
        
        if missing:
            print(f"{Colors.YELLOW}[!]{Colors.NC} Missing dependencies: {' '.join(missing)}")
            self.log.info("Installing missing dependencies...")
            
            try:
                self.runner.run(['apt-get', 'update', '-qq'], timeout=300)
                self.runner.run(['apt-get', 'install', '-y'] + missing, timeout=600)
                
                # Verify installation
                failed = []
                for cmd in missing:
                    if cmd == 'jq':
                        # Check both command and package
                        if not self.runner.check_command('jq'):
                            returncode, stdout, _ = self.runner.run(['dpkg', '-l', 'jq'], timeout=5)
                            if returncode != 0 or 'jq' not in stdout or 'ii' not in stdout:
                                failed.append('jq')
                    else:
                        if not self.runner.check_command(cmd):
                            failed.append(cmd)
                
                if failed:
                    print(f"{Colors.RED}[✗]{Colors.NC} Failed to install: {' '.join(failed)}")
                    sys.exit(1)
                
                print(f"{Colors.GREEN}[✓]{Colors.NC} Dependencies installed")
            except Exception as e:
                print(f"{Colors.RED}[✗]{Colors.NC} Failed to install dependencies: {e}")
                sys.exit(1)
    
    def show_banner(self):
        """Display the installer banner"""
        os.system('clear')
        print(BANNER)
        print()
    
    def register_step(self, step: BaseStep):
        """Register a step to be executed"""
        self.steps.append(step)
    
    def run(self, start_step: int = 0):
        """Run the installation"""
        self.show_banner()
        
        self.check_root()
        self.check_dependencies()
        
        self.log.info(f"Installation log: {self.log_file}")
        self.log.info(f"State file: {self.state_file}")
        print()
        
        # Ask about resuming if we have a saved state
        if start_step > 0:
            self.log.info(f"Resuming installation from step {start_step}")
            if not self.prompt.prompt_yesno("Continue from where you left off?", default="yes"):
                start_step = 0
                self.log.info("Starting from beginning")
        
        # Execute steps
        for step in self.steps:
            if step.step_number >= start_step:
                try:
                    step.run()
                except Exception as e:
                    self.log.error(f"Installation failed at step {step.step_number}: {e}")
                    raise
        
        print()
        self.log.success("━" * 78)
        self.log.success("Installation completed successfully!")
        self.log.success("━" * 78)
        print()
        
        # Display summary with important information
        self.display_summary()
    
    def display_summary(self):
        """Display important system information after installation"""
        print()
        self.log.success("╔" + "═" * 76 + "╗")
        self.log.success("║" + " " * 20 + "CHRONOVAULT INSTALLATION COMPLETE" + " " * 24 + "║")
        self.log.success("╚" + "═" * 76 + "╝")
        print()
        
        # Get token from control.env
        token = "Not found"
        control_env_path = '/opt/chronovault/env/control.env'
        if os.path.exists(control_env_path):
            try:
                with open(control_env_path, 'r') as f:
                    for line in f:
                        if line.startswith('CHRONOVAULT_UI_TOKEN='):
                            token = line.split('=', 1)[1].strip()
                            break
            except Exception as e:
                self.log.warning(f"Could not read token: {e}")
        
        # Get IP address
        ip_address = "Unknown"
        try:
            returncode, stdout, _ = self.runner.run(['hostname', '-I'], timeout=5)
            if returncode == 0 and stdout.strip():
                ip_address = stdout.strip().split()[0]  # Get first IP
        except Exception:
            pass
        
        # Get DuckDNS subdomain from config
        subdomain = self.config.get('DUCKDNS_SUBDOMAIN', 'chronovault')
        full_domain = f"{subdomain}.duckdns.org"
        
        # Check if email notifications are configured
        email_configured = False
        smtp_to = "Not configured"
        if os.path.exists(control_env_path):
            try:
                with open(control_env_path, 'r') as f:
                    for line in f:
                        if line.startswith('CHRONOVAULT_SMTP_ENABLED='):
                            if 'true' in line.lower():
                                email_configured = True
                        elif line.startswith('CHRONOVAULT_SMTP_TO='):
                            smtp_to = line.split('=', 1)[1].strip()
            except Exception:
                pass
        
        # Display information
        print()
        self.log.info(f"{Colors.CYAN}{Colors.BOLD}━━━ NEXT STEPS ━━━{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}1. Initial Setup:{Colors.NC}")
        self.log.info(f"   • Access Immich at {Colors.GREEN}http://{full_domain}:2283{Colors.NC} and complete initial setup")
        self.log.info(f"   • Access Nextcloud at {Colors.GREEN}http://{full_domain}:8080{Colors.NC} and complete initial setup")
        print()
        
        self.log.info(f"{Colors.CYAN}2. Twingate Remote Access:{Colors.NC}")
        twingate_network = self.config.get('TWINGATE_NETWORK', 'Not configured')
        if twingate_network != 'Not configured':
            self.log.info(f"   • Network: {Colors.GREEN}{twingate_network}{Colors.NC}")
            self.log.info(f"   • In Twingate Admin Console, find 'Add Resource' and add the following 3 resources:")
            print()
            self.log.info(f"     {Colors.CYAN}1. Label: {Colors.GREEN}immich{Colors.NC}")
            self.log.info(f"        Address: {Colors.GREEN}http://{full_domain}{Colors.NC}")
            self.log.info(f"        Click 'Port' and add: {Colors.GREEN}2283{Colors.NC}")
            print()
            self.log.info(f"     {Colors.CYAN}2. Label: {Colors.GREEN}nextcloud{Colors.NC}")
            self.log.info(f"        Address: {Colors.GREEN}http://{full_domain}{Colors.NC}")
            self.log.info(f"        Click 'Port' and add: {Colors.GREEN}8080{Colors.NC}")
            print()
            self.log.info(f"     {Colors.CYAN}3. Label: {Colors.GREEN}management UI{Colors.NC}")
            self.log.info(f"        Address: {Colors.GREEN}http://{full_domain}{Colors.NC}")
            self.log.info(f"        Click 'Port' and add: {Colors.GREEN}8787{Colors.NC}")
            print()
            self.log.info(f"   • Install Twingate client on your devices")
        else:
            self.log.info(f"   • {Colors.YELLOW}Twingate not configured{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}3. Nextcloud – Add ChronoVault External Site:{Colors.NC}")
        self.log.info(f"   • Log in to Nextcloud as an administrator")
        self.log.info(f"   • Navigate to {Colors.GREEN}Apps{Colors.NC}")
        self.log.info(f"   • Search for {Colors.GREEN}External sites{Colors.NC}")
        self.log.info(f"   • Click {Colors.GREEN}Download and enable{Colors.NC} (or {Colors.GREEN}Enable{Colors.NC} if already installed)")
        self.log.info(f"   • Open {Colors.GREEN}Administration settings{Colors.NC}")
        self.log.info(f"   • In the left sidebar, select {Colors.GREEN}External sites{Colors.NC} under the Administration section")
        self.log.info(f"   • Click {Colors.GREEN}Add{Colors.NC} and configure the following:")
        print()
        self.log.info(f"     {Colors.CYAN}Name:{Colors.NC} {Colors.GREEN}ChronoVault{Colors.NC}")
        self.log.info(f"     {Colors.CYAN}URL:{Colors.NC} {Colors.GREEN}http://{full_domain}:8787/?t={token}{Colors.NC}")
        print()
        self.log.info(f"   {Colors.CYAN}Direct Access (Without Nextcloud):{Colors.NC}")
        self.log.info(f"   • To access the ChronoVault management interface directly in a browser, use:")
        self.log.info(f"     {Colors.GREEN}http://{full_domain}:8787/?t={token}{Colors.NC}")
        print()
        self.log.info(f"   {Colors.CYAN}Management UI Capabilities:{Colors.NC}")
        self.log.info(f"   • View backup status and available restore points")
        self.log.info(f"   • Monitor system health")
        self.log.info(f"   • Verify how far back backups are retained")
        print()
        
        self.log.info(f"{Colors.CYAN}4. Monitoring:{Colors.NC}")
        if email_configured:
            self.log.info(f"   • Email notifications are enabled and will alert you of issues")
        else:
            self.log.info(f"   • {Colors.YELLOW}Consider configuring email notifications in Step 17{Colors.NC}")
        self.log.info(f"   • Check logs: {Colors.GREEN}sudo journalctl -u chronovault-control.service{Colors.NC}")
        self.log.info(f"   • Check backup logs: {Colors.GREEN}/var/log/chronovault/backup.log{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}5. Useful Commands:{Colors.NC}")
        self.log.info(f"   • View all containers: {Colors.GREEN}sudo docker ps -a{Colors.NC}")
        self.log.info(f"   • Check backup timer: {Colors.GREEN}sudo systemctl status chronovault-backup.timer{Colors.NC}")
        self.log.info(f"   • Check notification timer: {Colors.GREEN}sudo systemctl status chronovault-notify.timer{Colors.NC}")
        self.log.info(f"   • Manual backup: {Colors.GREEN}sudo /opt/chronovault/scripts/chronovault-backup-run{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}{Colors.BOLD}━━━ ACCESS INFORMATION ━━━{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Control UI Access Token:{Colors.NC}")
        self.log.info(f"  {Colors.YELLOW}{token}{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}System IP Address:{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}{ip_address}{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}DuckDNS Domain:{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}{full_domain}{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}{Colors.BOLD}━━━ SERVICE URLs ━━━{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Immich (Photo Server):{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}http://{full_domain}:2283{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Nextcloud (Document Server):{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}http://{full_domain}:8080{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Control UI (Management Dashboard):{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}http://{full_domain}:8787/?t={token}{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}{Colors.BOLD}━━━ AUTOMATED SCHEDULES ━━━{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Backup Schedule:{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}Daily at 2:00 AM (America/New_York){Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}System Updates:{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}Weekly on Sunday at 4:00 AM (America/New_York){Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Container Updates (Watchtower):{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}Daily at 5:00 AM (America/New_York){Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Email Notifications:{Colors.NC}")
        if email_configured:
            self.log.info(f"  {Colors.GREEN}Enabled - Notifications sent to: {smtp_to}{Colors.NC}")
            self.log.info(f"  {Colors.GREEN}Check interval: Every 5 minutes{Colors.NC}")
        else:
            self.log.info(f"  {Colors.YELLOW}Not configured{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}{Colors.BOLD}━━━ IMPORTANT FILE LOCATIONS ━━━{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Configuration Files:{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/env/control.env{Colors.NC} - Control service config")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/env/immich.env{Colors.NC} - Immich config")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/env/nextcloud.env{Colors.NC} - Nextcloud config")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/env/duckdns.env{Colors.NC} - DuckDNS config")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/env/twingate.env{Colors.NC} - Twingate config")
        print()
        
        self.log.info(f"{Colors.CYAN}Docker Compose Files:{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/compose/duckdns/{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/compose/immich/{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/compose/nextcloud/{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/compose/twingate/{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/compose/control/{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}/opt/chronovault/compose/watchtower/{Colors.NC}")
        print()
        
        self.log.info(f"{Colors.CYAN}Storage Mounts:{Colors.NC}")
        self.log.info(f"  {Colors.GREEN}/mnt/primary{Colors.NC} - Primary encrypted storage")
        self.log.info(f"  {Colors.GREEN}/mnt/backup{Colors.NC} - Backup encrypted storage")
        print()

