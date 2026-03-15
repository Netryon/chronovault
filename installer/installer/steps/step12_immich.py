"""Step 12: Install Immich"""

import os
from installer.base import BaseStep


class Step12InstallImmich(BaseStep):
    """Step 12: Install Immich"""
    
    @property
    def step_number(self) -> int:
        return 12
    
    @property
    def step_name(self) -> str:
        return "Install Immich"
    
    def execute(self) -> bool:
        """Install Immich photo server"""
        # Use v2 (tracks latest stable major version) as per guide
        # The guide uses v2 which automatically gets the latest version
        immich_version = "v2"
        self.log.info(f"Using Immich version: {immich_version} (tracks latest stable)")
        
        # Get configuration
        timezone = self.config.get('TIMEZONE', 'America/Toronto')
        if 'TIMEZONE' not in self.config:
            timezone = self.prompt.prompt("Enter timezone", default=timezone)
            self.config['TIMEZONE'] = timezone
        
        db_password = self.config.get('IMMICH_DB_PASSWORD')
        if not db_password:
            db_password = self.prompt.prompt("Enter Immich database password", is_password=True)
            self.config['IMMICH_DB_PASSWORD'] = db_password
        
        # Create env file - EXACT from guide, only password and timezone changed
        env_content = f"""# Chronovault Immich env
UPLOAD_LOCATION=/mnt/primary/apps/immich/library
DB_DATA_LOCATION=/mnt/primary/apps/immich/postgres
TZ={timezone}

# Immich version (v2 tracks stable major)
IMMICH_VERSION={immich_version}

# Database credentials (letters+numbers only recommended)
DB_USERNAME=postgres
DB_DATABASE_NAME=immich
DB_PASSWORD={db_password}
"""
        
        with open('/opt/chronovault/env/immich.env', 'w') as f:
            f.write(env_content)
        os.chmod('/opt/chronovault/env/immich.env', 0o600)
        
        # Create compose file - EXACT from guide, only version number changed
        compose_content = f"""services:
 immich-server:
   image: ghcr.io/immich-app/immich-server:${{IMMICH_VERSION:-{immich_version}}}
   container_name: immich-server
   env_file:
     - /opt/chronovault/env/immich.env
   ports:
     - "2283:2283"
   depends_on:
     - redis
     - database
   restart: unless-stopped
   volumes:
     - /mnt/primary/apps/immich/upload:/usr/src/app/upload
     - /mnt/primary/apps/immich/library:/usr/src/app/upload/library

 immich-machine-learning:
   image: ghcr.io/immich-app/immich-machine-learning:${{IMMICH_VERSION:-{immich_version}}}
   container_name: immich-machine-learning
   env_file:
     - /opt/chronovault/env/immich.env
   restart: unless-stopped

 redis:
   image: redis:7-alpine
   container_name: immich-redis
   restart: unless-stopped

 database:
   image: ghcr.io/immich-app/postgres:16-vectorchord0.3.0-pgvectors0.3.0
   container_name: immich-postgres
   env_file:
     - /opt/chronovault/env/immich.env
   restart: unless-stopped
   volumes:
     - /mnt/primary/apps/immich/postgres:/var/lib/postgresql/data

networks:
 default:
   name: immich
"""
        
        # Create directory for Immich compose file
        immich_dir = '/opt/chronovault/compose/immich'
        os.makedirs(immich_dir, exist_ok=True)
        
        with open(f'{immich_dir}/docker-compose.yml', 'w') as f:
            f.write(compose_content)
        
        # Start Immich
        self.runner.run(['docker', 'compose', 'up', '-d'], cwd=immich_dir, timeout=300)
        
        self.log.info("Waiting for Immich to start (this may take a minute)...")
        import time
        time.sleep(30)
        
        # Verify containers are running
        returncode, stdout, _ = self.runner.run(['docker', 'ps'], timeout=10)
        containers_running = ['immich-server' in stdout, 'immich-postgres' in stdout, 'immich-redis' in stdout]
        
        if not all(containers_running):
            self.log.warning("Some Immich containers may not be running yet")
            self.log.info("Check status: docker ps | grep immich")
            self.log.info("Check logs: docker logs immich-server")
            
            # Check for errors
            returncode, stderr, _ = self.runner.run(['docker', 'logs', 'immich-server', '--tail', '50'], timeout=10)
            if 'error' in stderr.lower() or 'failed' in stderr.lower():
                self.log.error("Immich server has errors. Check logs: docker logs immich-server")
                return False
        
        # Check if API is responding (optional - may take time)
        try:
            import urllib.request
            urllib.request.urlopen('http://localhost:2283/api/server-info/ping', timeout=10)
            self.log.success("Immich is running and API is responding!")
        except Exception:
            self.log.warning("Immich containers are running but API not ready yet.")
            self.log.info("This is normal - wait 1-2 minutes, then check: docker logs immich-server")
            self.log.info("Once ready, access Immich at: http://localhost:2283")
        
        subdomain = self.config.get('DUCKDNS_SUBDOMAIN', 'chronovault')
        self.log.info(f"Access Immich at: http://{subdomain}.duckdns.org:2283")
        self.log.info("Wait 1-2 minutes for first-time initialization, then create admin account")
        
        return True
