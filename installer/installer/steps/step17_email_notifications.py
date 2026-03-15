"""Step 17: Setup Email Notifications"""

import os
import shutil
import re
from installer.base import BaseStep


class Step17EmailNotifications(BaseStep):
    """Step 17: Setup Email Notifications"""
    
    @property
    def step_number(self) -> int:
        return 17
    
    @property
    def step_name(self) -> str:
        return "Setup Email Notifications"
    
    def _validate_email(self, email: str) -> bool:
        """Basic email validation"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    
    def execute(self) -> bool:
        """Setup email notifications with SMTP configuration"""
        
        self.log.info("Email Notification Setup")
        self.log.info("=" * 50)
        print()
        
        # 1. Get sender email
        while True:
            sender_email = self.prompt.prompt("Enter sender email address (for SMTP authentication)")
            if not sender_email:
                self.log.error("Sender email cannot be empty")
                continue
            if not self._validate_email(sender_email):
                self.log.error("Invalid email format. Please enter a valid email address.")
                continue
            break
        
        self.config['SMTP_SENDER_EMAIL'] = sender_email
        
        # 2. Show app password instructions
        print()
        self.log.info("To use Gmail, you need to create an App Password.")
        self.log.info("Instructions: https://support.google.com/mail/answer/185833?hl=en")
        print()
        input("Press Enter when you have your app password ready...")
        
        # 3. Get app password (remove spaces)
        while True:
            app_password = self.prompt.prompt("Enter Gmail app password", is_password=True)
            if not app_password:
                self.log.error("App password cannot be empty")
                continue
            # Remove spaces from app password
            app_password_clean = app_password.replace(' ', '')
            if app_password_clean != app_password:
                self.log.info(f"Removed spaces from app password (was {len(app_password)} chars, now {len(app_password_clean)} chars)")
            break
        
        self.config['SMTP_APP_PASSWORD'] = app_password_clean
        
        # 4. Get receiver email (validate different from sender)
        while True:
            receiver_email = self.prompt.prompt("Enter receiver email address (for notifications)")
            if not receiver_email:
                self.log.error("Receiver email cannot be empty")
                continue
            if not self._validate_email(receiver_email):
                self.log.error("Invalid email format. Please enter a valid email address.")
                continue
            if receiver_email.lower() == sender_email.lower():
                self.log.error("WARNING: Sender and receiver emails cannot be the same!")
                self.log.error("Please use a different email address for receiving notifications.")
                continue
            break
        
        self.config['SMTP_RECEIVER_EMAIL'] = receiver_email
        
        # 5. Append SMTP configuration to control.env
        self.log.info("Adding SMTP configuration to control.env...")
        control_env_path = '/opt/chronovault/env/control.env'
        
        if not os.path.exists(control_env_path):
            self.log.error(f"control.env not found: {control_env_path}")
            self.log.error("Please ensure Step 15 completed successfully")
            return False
        
        # Read existing content
        with open(control_env_path, 'r') as f:
            existing_content = f.read()
        
        # Append SMTP configuration
        smtp_config = f"""
# =========================
# SMTP Configuration (Chronovault)
# =========================
CHRONOVAULT_SMTP_ENABLED=true
CHRONOVAULT_SMTP_HOST=smtp.gmail.com
CHRONOVAULT_SMTP_PORT=587
CHRONOVAULT_SMTP_USERNAME={sender_email}
CHRONOVAULT_SMTP_PASSWORD={app_password_clean}
CHRONOVAULT_SMTP_USE_TLS=true
CHRONOVAULT_SMTP_FROM={sender_email}
CHRONOVAULT_SMTP_TO={receiver_email}

# =========================
# Notifications tuning
# =========================
CHRONOVAULT_DISK_USED_WARN_PCT=90
CHRONOVAULT_PERSISTENT_REPEAT_SEC=86400
"""
        
        # Write back with new content
        with open(control_env_path, 'w') as f:
            f.write(existing_content.rstrip() + '\n' + smtp_config)
        
        self.log.success("SMTP configuration added to control.env")
        
        # 6. Restart control service to load new env
        self.log.info("Restarting control service to load environment variables...")
        returncode, _, stderr = self.runner.run(['systemctl', 'restart', 'chronovault-control.service'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to restart control service: {stderr}")
            return False
        
        # 7. Copy mailer.py
        self.log.info("Copying mailer.py...")
        installer_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        installer_scripts_dir = os.path.join(installer_dir, 'scripts')
        mailer_src = os.path.join(installer_scripts_dir, 'mailer.py')
        mailer_dst = '/opt/chronovault/control/app/mailer.py'
        
        if not os.path.exists(mailer_src):
            self.log.error(f"mailer.py not found: {mailer_src}")
            return False
        
        shutil.copy2(mailer_src, mailer_dst)
        self.runner.run(['chown', 'chronovaultctl:chronovaultctl', mailer_dst], timeout=10)
        self.runner.run(['chmod', '644', mailer_dst], timeout=10)
        self.log.success("mailer.py copied and permissions set")
        
        # 8. Restart control service again
        self.log.info("Restarting control service again...")
        returncode, _, stderr = self.runner.run(['systemctl', 'restart', 'chronovault-control.service'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to restart control service: {stderr}")
            return False
        
        # 9. Copy notify.py
        self.log.info("Copying notify.py...")
        notify_src = os.path.join(installer_scripts_dir, 'notify.py')
        notify_dst = '/opt/chronovault/control/app/notify.py'
        
        if not os.path.exists(notify_src):
            self.log.error(f"notify.py not found: {notify_src}")
            return False
        
        shutil.copy2(notify_src, notify_dst)
        self.runner.run(['chmod', '755', notify_dst], timeout=10)
        self.log.success("notify.py copied and made executable")
        
        # 10. Create/verify state directory permissions
        self.log.info("Creating/verifying state directory permissions...")
        state_dir = '/var/lib/chronovault/state'
        os.makedirs(state_dir, exist_ok=True)
        self.runner.run(['chown', '-R', 'chronovaultctl:chronovaultctl', state_dir], timeout=10)
        self.runner.run(['chmod', '750', state_dir], timeout=10)
        self.log.success("State directory permissions set")
        
        # 11. Copy systemd service and timer files
        self.log.info("Copying systemd service and timer files...")
        systemd_files = [
            'chronovault-notify.service',
            'chronovault-notify.timer'
        ]
        
        for systemd_file in systemd_files:
            src = os.path.join(installer_scripts_dir, systemd_file)
            dst = f'/etc/systemd/system/{systemd_file}'
            
            if not os.path.exists(src):
                self.log.error(f"{systemd_file} not found: {src}")
                return False
            
            shutil.copy2(src, dst)
            self.runner.run(['chmod', '644', dst], timeout=10)
            self.log.info(f"Copied {systemd_file}")
        
        # 12. Reload systemd and enable timer
        self.log.info("Reloading systemd daemon...")
        returncode, _, stderr = self.runner.run(['systemctl', 'daemon-reload'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to reload systemd: {stderr}")
            return False
        
        self.log.info("Enabling and starting notification timer...")
        returncode, _, stderr = self.runner.run(['systemctl', 'enable', '--now', 'chronovault-notify.timer'], timeout=30)
        if returncode != 0:
            self.log.error(f"Failed to enable notification timer: {stderr}")
            return False
        
        self.log.success("Email notification setup completed!")
        self.log.info("Notifications will run every 5 minutes")
        
        return True
