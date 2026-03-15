"""Step 14: Install Nextcloud"""

import os
from installer.base import BaseStep


class Step14InstallNextcloud(BaseStep):
    """Step 14: Install Nextcloud"""
    
    @property
    def step_number(self) -> int:
        return 14
    
    @property
    def step_name(self) -> str:
        return "Install Nextcloud"
    
    def execute(self) -> bool:
        """Install Nextcloud document server"""
        # Get latest version and extract major version (e.g., "32.0.5-apache" -> "32")
        nc_version_full = self.installer.version_checker.get_latest_nextcloud_version()
        # Extract major version number (e.g., "32.0.5-apache" -> "32")
        nc_version_major = nc_version_full.split('.')[0] if '.' in nc_version_full else nc_version_full.split('-')[0]
        nc_version = f"{nc_version_major}-apache"  # Guide uses format like "32-apache"
        self.log.info(f"Using Nextcloud version: {nc_version}")
        
        # Get configuration
        timezone = self.config.get('TIMEZONE', 'America/Toronto')
        db_password = self.config.get('NEXTCLOUD_DB_PASSWORD')
        if not db_password:
            db_password = self.prompt.prompt("Enter Nextcloud database password", is_password=True)
            self.config['NEXTCLOUD_DB_PASSWORD'] = db_password
        
        admin_user = self.config.get('NEXTCLOUD_ADMIN_USER', 'admin')
        if 'NEXTCLOUD_ADMIN_USER' not in self.config:
            admin_user = self.prompt.prompt("Enter Nextcloud admin username", default=admin_user)
            self.config['NEXTCLOUD_ADMIN_USER'] = admin_user
        
        admin_password = self.config.get('NEXTCLOUD_ADMIN_PASSWORD')
        if not admin_password:
            admin_password = self.prompt.prompt("Enter Nextcloud admin password", is_password=True)
            self.config['NEXTCLOUD_ADMIN_PASSWORD'] = admin_password
        
        # Create env file - EXACT from guide, only passwords changed
        env_content = f"""# Chronovault Nextcloud env
TZ={timezone}


# Database (PostgreSQL)
POSTGRES_DB=nextcloud
POSTGRES_USER=nextcloud
POSTGRES_PASSWORD={db_password}


# Nextcloud admin bootstrap (first-run only)
NEXTCLOUD_ADMIN_USER={admin_user}
NEXTCLOUD_ADMIN_PASSWORD={admin_password}
"""
        
        with open('/opt/chronovault/env/nextcloud.env', 'w') as f:
            f.write(env_content)
        os.chmod('/opt/chronovault/env/nextcloud.env', 0o600)
        
        # Create compose file - EXACT from updated guide, only version number changed
        compose_content = f"""services:
  nextcloud-db:
    image: postgres:16-alpine
    container_name: nextcloud-postgres
    restart: unless-stopped
    env_file:
      - /opt/chronovault/env/nextcloud.env
    volumes:
      - /mnt/primary/apps/postgres/nextcloud:/var/lib/postgresql/data

  nextcloud:
    image: nextcloud:{nc_version}
    container_name: nextcloud
    restart: unless-stopped
    depends_on:
      - nextcloud-db
    env_file:
      - /opt/chronovault/env/nextcloud.env
    environment:
      - POSTGRES_HOST=nextcloud-db
      - NEXTCLOUD_DATA_DIR=/var/www/html/data
    ports:
      - "8080:80"
    volumes:
      - /mnt/primary/apps/nextcloud/html:/var/www/html
      - /mnt/primary/apps/nextcloud/data:/var/www/html/data

networks:
  default:
    name: nextcloud
"""
        
        # Create directory for Nextcloud compose file
        nextcloud_dir = '/opt/chronovault/compose/nextcloud'
        os.makedirs(nextcloud_dir, exist_ok=True)
        
        with open(f'{nextcloud_dir}/docker-compose.yml', 'w') as f:
            f.write(compose_content)
        
        # Start Nextcloud - EXACT from guide (start both containers together)
        self.runner.run(['docker', 'compose', 'up', '-d'], cwd=nextcloud_dir, timeout=300)
        
        self.log.info("Waiting for Nextcloud to initialize (this may take 2-3 minutes)...")
        import time
        time.sleep(60)
        
        # Verify
        returncode, stdout, _ = self.runner.run(['docker', 'ps'], timeout=10)
        if 'nextcloud' in stdout:
            self.log.success("Nextcloud is running!")
            
            # Wait a bit more for config file to be created
            import time
            time.sleep(30)
            
            # Configure trusted domains
            self._configure_trusted_domains()
            
            # Restart Nextcloud to apply changes
            self.runner.run(['docker', 'restart', 'nextcloud'], timeout=30)
            
            subdomain = self.config.get('DUCKDNS_SUBDOMAIN', 'chronovault')
            full_domain = f"{subdomain}.duckdns.org"
            self.log.info(f"Access Nextcloud at: http://{full_domain}:8080")
            self.log.info("Wait 1-3 minutes for first-time initialization, then refresh")
        else:
            self.log.error("Nextcloud failed to start")
            return False
        
        return True
    
    def _configure_trusted_domains(self):
        """Configure Nextcloud trusted domains"""
        config_path = '/mnt/primary/apps/nextcloud/html/config/config.php'
        
        # Get full domain from user
        subdomain = self.config.get('DUCKDNS_SUBDOMAIN', 'chronovault')
        default_domain = f"{subdomain}.duckdns.org"
        full_domain = self.prompt.prompt("Enter full DuckDNS domain for Nextcloud", default=default_domain)
        
        if not os.path.exists(config_path):
            self.log.warning("Nextcloud config.php not found yet, will be created on first access")
            return
        
        try:
            # Read existing config
            with open(config_path, 'r') as f:
                content = f.read()
            
            # Check if trusted_domains already exists
            if "'trusted_domains'" in content:
                # Check if our domain is already in the list
                if full_domain in content:
                    self.log.info(f"Domain {full_domain} already in trusted_domains")
                    return
                
                # Find the trusted_domains array and add our domain
                import re
                # Pattern to match the array and add entry before closing
                pattern = r"('trusted_domains'\s*=>\s*array\s*\([^)]*)(\))"
                match = re.search(pattern, content, re.DOTALL)
                if match:
                    # Find the last number in the array
                    array_content = match.group(1)
                    # Extract the highest index
                    index_matches = re.findall(r"(\d+)\s*=>", array_content)
                    if index_matches:
                        next_index = str(int(max(index_matches, key=int)) + 1)
                    else:
                        next_index = "1"
                    
                    # Add the new entry
                    new_entry = f"{match.group(1)}\n  {next_index} => '{full_domain}',\n{match.group(2)}"
                    content = re.sub(pattern, new_entry, content, flags=re.DOTALL)
                else:
                    # Fallback: add before the closing of array
                    # Find the highest index
                    index_pattern = re.compile(r'\d+\s*=>')
                    index_matches = index_pattern.findall(content)
                    if index_matches:
                        # Extract numbers and find max
                        numbers = [int(match.split('=>')[0].strip()) for match in index_matches]
                        next_index = str(max(numbers) + 1)
                    else:
                        next_index = "1"
                    content = content.replace(
                        "  ),",
                        f"  {next_index} => '{full_domain}',\n  ),"
                    )
            else:
                # Add trusted_domains array before the closing );
                # Find the last ); before the final closing
                if ");" in content:
                    # Add before the last );
                    parts = content.rsplit(");", 1)
                    trusted_domains = f"""'trusted_domains' =>
array (
  0 => 'localhost',
  1 => '{full_domain}',
),
"""
                    content = parts[0] + trusted_domains + ");" + parts[1]
                else:
                    self.log.warning("Could not find insertion point for trusted_domains")
                    return
            
            # Write updated config
            with open(config_path, 'w') as f:
                f.write(content)
            
            self.log.success(f"Added {full_domain} to Nextcloud trusted_domains")
            
        except Exception as e:
            self.log.warning(f"Could not configure trusted_domains: {e}")
            self.log.info("You can manually edit /mnt/primary/apps/nextcloud/html/config/config.php")
