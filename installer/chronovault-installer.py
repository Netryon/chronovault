#!/usr/bin/env python3
"""
Chronovault Automated Installation Script (Python)

This script automates the complete Chronovault setup process with:
- Interactive prompts for all required information
- Version checking for Immich, Nextcloud, and Twingate
- Disk selection for Primary/Backup storage
- State tracking for resume after reboot
- Progress indicators and error handling

Usage: sudo python3 chronovault-installer.py
"""

import sys
import os

# Add installer package to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from installer.installer import ChronovaultInstaller
from installer.steps.step1_verify import Step1VerifySystem
from installer.steps.step2_packages import Step2InstallPackages
from installer.steps.step3_ssh import Step3ConfigureSSH
from installer.steps.step4_firewall import Step4ConfigureFirewall
from installer.steps.step5_folders import Step5CreateFolders
from installer.steps.step6_disks import Step6SelectDisks
from installer.steps.step7_encryption import Step7SetupEncryption
from installer.steps.step8_autounlock import Step8SetupAutoUnlock
from installer.steps.step9_directories import Step9CreateAppDirectories
from installer.steps.step10_docker import Step10InstallDocker
from installer.steps.step11_duckdns import Step11SetupDuckDNS
from installer.steps.step12_immich import Step12InstallImmich
from installer.steps.step13_twingate import Step13SetupTwingate
from installer.steps.step14_nextcloud import Step14InstallNextcloud
from installer.steps.step15_control import Step15SetupControl
from installer.steps.step16_initial_backup import Step16InitialBackup
from installer.steps.step17_email_notifications import Step17EmailNotifications
from installer.steps.step18_timers_watchtower import Step18TimersWatchtower


def main():
    """Main entry point"""
    # Initialize installer
    installer = ChronovaultInstaller()
    
    # Load current step
    start_step = installer._load_state()
    
    # Register all steps
    installer.register_step(Step1VerifySystem(installer))
    installer.register_step(Step2InstallPackages(installer))
    installer.register_step(Step3ConfigureSSH(installer))
    installer.register_step(Step4ConfigureFirewall(installer))
    installer.register_step(Step5CreateFolders(installer))
    installer.register_step(Step6SelectDisks(installer))
    installer.register_step(Step7SetupEncryption(installer))
    installer.register_step(Step8SetupAutoUnlock(installer))
    installer.register_step(Step9CreateAppDirectories(installer))
    installer.register_step(Step10InstallDocker(installer))
    installer.register_step(Step11SetupDuckDNS(installer))
    installer.register_step(Step12InstallImmich(installer))
    installer.register_step(Step13SetupTwingate(installer))
    installer.register_step(Step14InstallNextcloud(installer))
    installer.register_step(Step15SetupControl(installer))
    installer.register_step(Step16InitialBackup(installer))
    installer.register_step(Step17EmailNotifications(installer))
    installer.register_step(Step18TimersWatchtower(installer))
    
    # Run installation
    try:
        installer.run(start_step=start_step)
    except KeyboardInterrupt:
        installer.log.warning("\nInstallation interrupted by user")
        sys.exit(1)
    except Exception as e:
        installer.log.error(f"Installation failed: {e}")
        import traceback
        installer.log.error(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()
