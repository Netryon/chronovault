"""Step 15: Setup Chronovault Control Service"""

import os
import shutil
import subprocess
import secrets
import base64
from installer.base import BaseStep


class Step15SetupControl(BaseStep):
    """Step 15: Setup Chronovault Control Service"""
    
    @property
    def step_number(self) -> int:
        return 15
    
    @property
    def step_name(self) -> str:
        return "Setup Chronovault Control Service"
    
    def execute(self) -> bool:
        """Setup Chronovault Control Service with API and UI"""
        
        # 1. Create Chronovault state + status locations (SD card)
        self.log.info("Creating state and log directories...")
        os.makedirs('/var/lib/chronovault/state', exist_ok=True)
        os.makedirs('/var/log/chronovault', exist_ok=True)
        self.runner.run(['chown', '-R', 'root:root', '/var/lib/chronovault', '/var/log/chronovault'], timeout=30)
        self.runner.run(['chmod', '-R', '755', '/var/lib/chronovault', '/var/log/chronovault'], timeout=30)
        
        # 2. Copy scripts from installer/scripts to /opt/chronovault/scripts
        self.log.info("Copying backup and restore scripts...")
        script_files = [
            'chronovault_backup.py',
            'chronovault-backup-run',
            'chronovault_restore.py',
            'chronovault-restore'
        ]
        
        # Get the installer/scripts directory
        # __file__ is installer/steps/step15_control.py
        # Go up to installer/ directory, then into scripts/
        installer_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        installer_scripts_dir = os.path.join(installer_dir, 'scripts')
        target_scripts_dir = '/opt/chronovault/scripts'
        
        for script_file in script_files:
            src = os.path.join(installer_scripts_dir, script_file)
            dst = os.path.join(target_scripts_dir, script_file)
            if os.path.exists(src):
                shutil.copy2(src, dst)
                self.log.info(f"Copied {script_file}")
            else:
                self.log.warning(f"Script not found: {src}")
        
        # Lock scripts with permissions
        self.log.info("Setting script permissions...")
        for script_file in script_files:
            script_path = os.path.join(target_scripts_dir, script_file)
            if os.path.exists(script_path):
                self.runner.run(['chown', 'root:root', script_path], timeout=10)
                self.runner.run(['chmod', '700', script_path], timeout=10)
        
        # 3. Create control.env with generated token
        self.log.info("Creating control.env with secure token...")
        token = base64.b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        env_content = f"CHRONOVAULT_UI_TOKEN={token}\n"
        
        control_env_path = '/opt/chronovault/env/control.env'
        with open(control_env_path, 'w') as f:
            f.write(env_content)
        self.runner.run(['chown', 'root:root', control_env_path], timeout=10)
        self.runner.run(['chmod', '600', control_env_path], timeout=10)
        
        # 4. Create control.yml compose file
        self.log.info("Creating control.yml compose file...")
        compose_content = """services:
  chronovault-control:
    image: nginx:alpine
    container_name: chronovault-control
    restart: unless-stopped
    env_file:
      - /opt/chronovault/env/control.env
    ports:
      - "8787:80"
    volumes:
      - /var/lib/chronovault:/usr/share/nginx/html:ro
      - /opt/chronovault/control/nginx.conf.template:/etc/nginx/templates/nginx.conf.template:ro
    command: >
      /bin/sh -c "envsubst '$$CHRONOVAULT_UI_TOKEN' < /etc/nginx/templates/nginx.conf.template > /etc/nginx/nginx.conf
      && nginx -g 'daemon off;'"
"""
        
        # Create directory for control compose file
        control_dir = '/opt/chronovault/compose/control'
        os.makedirs(control_dir, exist_ok=True)
        
        compose_path = f'{control_dir}/docker-compose.yml'
        with open(compose_path, 'w') as f:
            f.write(compose_content)
        self.runner.run(['chown', 'root:root', compose_path], timeout=10)
        self.runner.run(['chmod', '644', compose_path], timeout=10)
        
        # 5. Start the service
        self.log.info("Starting control service...")
        returncode, stdout, stderr = self.runner.run(
            ['docker', 'compose', 'up', '-d'],
            cwd=control_dir,
            timeout=60
        )
        if returncode != 0:
            self.log.error(f"Failed to start control service: {stderr}")
            return False
        
        # 6. Update UFW rules
        self.log.info("Updating UFW firewall rules...")
        # Allow on eth0
        self.runner.run(['ufw', 'allow', 'in', 'on', 'eth0', 'to', 'any', 'port', '8787', 'proto', 'tcp'], timeout=10)
        # Deny everyone else
        self.runner.run(['ufw', 'deny', '8787/tcp'], timeout=10)
        
        # 7. Create nginx config template
        self.log.info("Creating nginx config template...")
        os.makedirs('/opt/chronovault/control', exist_ok=True)
        self.runner.run(['chown', 'root:root', '/opt/chronovault/control'], timeout=10)
        self.runner.run(['chmod', '700', '/opt/chronovault/control'], timeout=10)
        
        nginx_template = """events {}
http {
server {
listen 80;
# Require shared token on ALL requests
# Use query param: ?t=YOURTOKEN
set $required_token "${CHRONOVAULT_UI_TOKEN}";
if ($arg_t = "") { return 403; }
if ($arg_t != $required_token) { return 403; }
# Serve SD-stored status files (read-only mount)
location / {
root /usr/share/nginx/html;
autoindex off;
default_type application/json;
add_header Cache-Control "no-store";
try_files $uri =404;
}
}
}
"""
        
        nginx_template_path = '/opt/chronovault/control/nginx.conf.template'
        # Remove if it exists as a directory (from previous failed run)
        if os.path.exists(nginx_template_path) and os.path.isdir(nginx_template_path):
            os.rmdir(nginx_template_path)
        # Remove if it exists as a file (to ensure clean write)
        elif os.path.exists(nginx_template_path):
            os.remove(nginx_template_path)
        
        with open(nginx_template_path, 'w') as f:
            f.write(nginx_template)
        self.runner.run(['chown', 'root:root', nginx_template_path], timeout=10)
        self.runner.run(['chmod', '600', nginx_template_path], timeout=10)
        
        # 8. Create control service directory
        self.log.info("Creating control service directory...")
        os.makedirs('/opt/chronovault/control/app', exist_ok=True)
        self.runner.run(['chown', '-R', 'root:root', '/opt/chronovault/control'], timeout=10)
        self.runner.run(['chmod', '700', '/opt/chronovault/control'], timeout=10)
        
        # 9. Create dedicated host user for control execution
        self.log.info("Creating chronovaultctl user...")
        # Check if user already exists
        returncode, _, _ = self.runner.run(['id', 'chronovaultctl'], timeout=5)
        if returncode != 0:
            # User doesn't exist, create it
            self.runner.run(['useradd', '-m', '-r', '-s', '/usr/sbin/nologin', 'chronovaultctl'], timeout=10)
        
        # Set password for the user
        password = self.prompt.prompt("Enter password for chronovaultctl user", is_password=True)
        proc = subprocess.Popen(
            ['passwd', 'chronovaultctl'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = proc.communicate(input=f"{password}\n{password}\n", timeout=10)
        if proc.returncode != 0:
            self.log.error(f"Failed to set password for chronovaultctl: {stderr}")
            return False
        
        # Verify user is not locked
        returncode, stdout, _ = self.runner.run(['passwd', '-S', 'chronovaultctl'], timeout=10)
        if returncode == 0 and 'L' in stdout:
            self.log.warning("User is locked, unlocking...")
            self.runner.run(['passwd', '-u', 'chronovaultctl'], timeout=10)
        
        # Get UID and GID
        returncode, id_output, _ = self.runner.run(['id', 'chronovaultctl'], timeout=10)
        if returncode != 0:
            self.log.error("Failed to get chronovaultctl UID/GID")
            return False
        
        # Parse UID and GID from output: uid=999(chronovaultctl) gid=984(chronovaultctl)
        uid = None
        gid = None
        for part in id_output.split():
            if part.startswith('uid='):
                uid = part.split('=')[1].split('(')[0]
            elif part.startswith('gid='):
                gid = part.split('=')[1].split('(')[0]
        
        if not uid or not gid:
            self.log.error("Failed to parse UID/GID from id output")
            return False
        
        self.log.info(f"chronovaultctl UID: {uid}, GID: {gid}")
        
        # 10. Create compose environment file (UID/GID)
        self.log.info("Creating control-api.compose.env...")
        compose_env_content = f"""SV_UID={uid}
SV_GID={gid}
"""
        compose_env_path = '/opt/chronovault/env/control-api.compose.env'
        with open(compose_env_path, 'w') as f:
            f.write(compose_env_content)
        self.runner.run(['chown', 'root:root', compose_env_path], timeout=10)
        self.runner.run(['chmod', '644', compose_env_path], timeout=10)
        
        # 11. Create sudo allowlist
        self.log.info("Creating sudo allowlist for chronovaultctl...")
        sudoers_content = """Defaults:chronovaultctl !requiretty
chronovaultctl ALL=(root) NOPASSWD: /opt/chronovault/scripts/chronovault-backup-run
chronovaultctl ALL=(root) NOPASSWD: /opt/chronovault/scripts/chronovault-backup-mount
chronovaultctl ALL=(root) NOPASSWD: /opt/chronovault/scripts/chronovault-backup-umount
chronovaultctl ALL=(root) NOPASSWD: /opt/chronovault/scripts/chronovault-restore *
chronovaultctl ALL=(root) NOPASSWD: /usr/bin/touch /var/lib/chronovault/state/approve_once
chronovaultctl ALL=(root) NOPASSWD: /bin/rm -f /var/lib/chronovault/state/approve_once
chronovaultctl ALL=(root) NOPASSWD: /bin/systemctl start chronovault-backup.service
chronovaultctl ALL=(root) NOPASSWD: /bin/systemctl start chronovault-backup.service --no-block
"""
        
        sudoers_path = '/etc/sudoers.d/chronovaultctl'
        with open(sudoers_path, 'w') as f:
            f.write(sudoers_content)
        self.runner.run(['chmod', '440', sudoers_path], timeout=10)
        
        # Verify sudoers file
        returncode, _, stderr = self.runner.run(['visudo', '-c'], timeout=10)
        if returncode != 0:
            self.log.error(f"sudoers file validation failed: {stderr}")
            return False
        
        # 12. Change control.env owner
        self.log.info("Setting control.env ownership...")
        self.runner.run(['chown', 'root:chronovaultctl', control_env_path], timeout=10)
        self.runner.run(['chmod', '640', control_env_path], timeout=10)
        
        # 13. Create state directory (already done, but ensure permissions)
        self.log.info("Ensuring state directory permissions...")
        os.makedirs('/var/lib/chronovault/state', exist_ok=True)
        self.runner.run(['chown', 'root:root', '/var/lib/chronovault/state'], timeout=10)
        self.runner.run(['chmod', '700', '/var/lib/chronovault/state'], timeout=10)
        
        # 14. Install Python venv support
        self.log.info("Installing Python venv support...")
        self.runner.run(['apt-get', 'update', '-qq'], timeout=300)
        self.runner.run(['apt-get', 'install', '-y', 'python3-venv'], timeout=300)
        
        # 15. Create Control API directories
        self.log.info("Creating Control API directories...")
        os.makedirs('/opt/chronovault/control/app', exist_ok=True)
        self.runner.run(['chown', '-R', 'root:root', '/opt/chronovault/control'], timeout=10)
        self.runner.run(['chmod', '755', '/opt/chronovault/control'], timeout=10)
        self.runner.run(['chmod', '755', '/opt/chronovault/control/app'], timeout=10)
        
        # 16. Create Python virtual environment
        self.log.info("Creating Python virtual environment...")
        venv_path = '/opt/chronovault/control/venv'
        returncode, _, stderr = self.runner.run(
            ['python3', '-m', 'venv', venv_path],
            timeout=60
        )
        if returncode != 0:
            self.log.error(f"Failed to create venv: {stderr}")
            return False
        
        # Upgrade pip
        self.runner.run(
            [f'{venv_path}/bin/pip', 'install', '--upgrade', 'pip'],
            timeout=120
        )
        
        # Install dependencies
        self.log.info("Installing Python dependencies...")
        returncode, _, stderr = self.runner.run(
            [f'{venv_path}/bin/pip', 'install', 'fastapi', 'uvicorn'],
            timeout=300
        )
        if returncode != 0:
            self.log.error(f"Failed to install dependencies: {stderr}")
            return False
        
        # 17. Copy main.py to control/app
        self.log.info("Copying main.py to control app directory...")
        main_src = os.path.join(installer_scripts_dir, 'main.py')
        main_dst = '/opt/chronovault/control/app/main.py'
        if os.path.exists(main_src):
            shutil.copy2(main_src, main_dst)
            self.runner.run(['chown', 'root:root', main_dst], timeout=10)
            self.runner.run(['chmod', '644', main_dst], timeout=10)
        else:
            self.log.error(f"main.py not found: {main_src}")
            return False
        
        # 18. Create systemd service file
        self.log.info("Creating systemd service file...")
        service_content = """[Unit]
Description=Chronovault Control API
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=chronovaultctl
Group=chronovaultctl
WorkingDirectory=/opt/chronovault/control/app
EnvironmentFile=/opt/chronovault/env/control.env
ExecStart=/opt/chronovault/control/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8787
Restart=always
RestartSec=2
# =========================
# Security Hardening
# =========================
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
# Allow sudo + runtime state
ReadWritePaths=/var/lib/chronovault/state
ReadWritePaths=/var/lib/chronovault
ReadWritePaths=/var/log/chronovault
ReadWritePaths=/mnt/backup
ReadWritePaths=/mnt/primary
ReadWritePaths=/run
ReadWritePaths=/run/sudo
# Allow read-only access to required data
ReadOnlyPaths=/opt/chronovault/scripts
ReadOnlyPaths=/opt/chronovault/control/app
ReadOnlyPaths=/opt/chronovault/env
# DO NOT enable NoNewPrivileges
# sudo is REQUIRED by design
NoNewPrivileges=no
[Install]
WantedBy=multi-user.target
"""
        
        service_path = '/etc/systemd/system/chronovault-control.service'
        with open(service_path, 'w') as f:
            f.write(service_content)
        self.runner.run(['chmod', '644', service_path], timeout=10)
        
        # Stop nginx container first (since systemd service will use port 8787)
        self.log.info("Stopping nginx container (will be replaced by systemd service)...")
        self.runner.run(
            ['docker', 'compose', 'down'],
            cwd=control_dir,
            timeout=30
        )
        
        # Reload systemd and enable service
        self.runner.run(['systemctl', 'daemon-reload'], timeout=30)
        self.runner.run(['systemctl', 'enable', '--now', 'chronovault-control.service'], timeout=30)
        
        # 19. Set permissions for logs
        self.log.info("Setting log directory permissions...")
        self.runner.run(['chgrp', 'chronovaultctl', '/var/log/chronovault'], timeout=10)
        self.runner.run(['chmod', '2775', '/var/log/chronovault'], timeout=10)
        
        # Create backup.log if it doesn't exist
        backup_log_path = '/var/log/chronovault/backup.log'
        if not os.path.exists(backup_log_path):
            with open(backup_log_path, 'w') as f:
                f.write('')
        self.runner.run(['chgrp', 'chronovaultctl', backup_log_path], timeout=10)
        self.runner.run(['chmod', '664', backup_log_path], timeout=10)
        
        # 20. Copy UI folder from installer/scripts/ui to /opt/chronovault/ui
        self.log.info("Copying UI folder from installer...")
        ui_src = os.path.join(installer_scripts_dir, 'ui')
        ui_dst = '/opt/chronovault/ui'
        
        # Remove destination if it exists (to ensure clean copy)
        if os.path.exists(ui_dst):
            shutil.rmtree(ui_dst)
        
        # Copy entire UI folder with all contents
        if os.path.exists(ui_src):
            shutil.copytree(ui_src, ui_dst)
            self.log.info("UI folder copied successfully")
        else:
            self.log.error(f"UI folder not found: {ui_src}")
            return False
        
        # Check if sysadmin user exists
        returncode, _, _ = self.runner.run(['id', 'sysadmin'], timeout=5)
        if returncode == 0:
            self.runner.run(['chown', '-R', 'sysadmin:sysadmin', ui_dst], timeout=30)
        else:
            # If sysadmin doesn't exist, use root
            self.log.warning("sysadmin user not found, using root for UI directory")
            self.runner.run(['chown', '-R', 'root:root', ui_dst], timeout=30)
        
        self.runner.run(['chmod', '-R', '755', ui_dst], timeout=30)
        
        # 21. Update control.env with additional variables
        self.log.info("Updating control.env with additional variables...")
        # Read existing token
        with open(control_env_path, 'r') as f:
            existing_content = f.read().strip()
        
        # Append new variables
        updated_env_content = existing_content + "\n"
        updated_env_content += "CHRONOVAULT_UI_DIR=/opt/chronovault/ui\n"
        updated_env_content += "PYTHONDONTWRITEBYTECODE=1\n"
        
        with open(control_env_path, 'w') as f:
            f.write(updated_env_content)
        # Keep ownership and permissions
        self.runner.run(['chown', 'root:chronovaultctl', control_env_path], timeout=10)
        self.runner.run(['chmod', '640', control_env_path], timeout=10)
        
        # 22. Restart the service
        self.log.info("Restarting chronovault-control service...")
        self.runner.run(['systemctl', 'daemon-reload'], timeout=30)
        self.runner.run(['systemctl', 'restart', 'chronovault-control.service'], timeout=30)
        
        # Verify service status
        returncode, stdout, _ = self.runner.run(
            ['systemctl', 'status', 'chronovault-control.service', '--no-pager', '-l'],
            timeout=10
        )
        if returncode == 0:
            self.log.info("Service status:")
            self.log.info(stdout)
        else:
            self.log.warning("Could not get service status, but continuing...")
        
        self.log.success("Chronovault Control Service setup completed!")
        self.log.info("The control API is now running on port 8787")
        self.log.info(f"Access token saved in: {control_env_path}")
        
        return True
