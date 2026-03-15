"""Step 11: Setup DuckDNS"""

import os
import socket
from installer.base import BaseStep


class Step11SetupDuckDNS(BaseStep):
    """Step 11: Setup DuckDNS"""
    
    @property
    def step_number(self) -> int:
        return 11
    
    @property
    def step_name(self) -> str:
        return "Setup DuckDNS"
    
    def execute(self) -> bool:
        """Setup DuckDNS dynamic DNS"""
        self.log.info("DuckDNS Setup Instructions:")
        self.log.info("1. Go to https://www.duckdns.org/")
        self.log.info("2. Sign in with your preferred OAuth provider")
        self.log.info("3. Create a subdomain (e.g., chronovault)")
        self.log.info("4. Copy your token from the dashboard")
        print()
        
        input("Press Enter when you have your DuckDNS token ready...")
        
        subdomain = self.prompt.prompt("Enter DuckDNS subdomain", default="chronovault")
        token = self.prompt.prompt("Enter DuckDNS token", is_password=True)
        
        self.config['DUCKDNS_SUBDOMAIN'] = subdomain
        self.config['DUCKDNS_TOKEN'] = token
        
        # Create env file - EXACT from guide, only token/subdomain changed
        env_content = f"""DUCKDNS_SUBDOMAIN={subdomain}
DUCKDNS_TOKEN={token}
UPDATE_INTERVAL_SECONDS=300
"""
        
        with open('/opt/chronovault/env/duckdns.env', 'w') as f:
            f.write(env_content)
        os.chmod('/opt/chronovault/env/duckdns.env', 0o600)
        
        # Create compose file - EXACT from guide with escaped quotes
        compose_content = """services:
  duckdns:
    image: alpine:3.20
    container_name: duckdns
    restart: unless-stopped
    env_file:
      - /opt/chronovault/env/duckdns.env
    network_mode: host
    command: >
      sh -c "apk add --no-cache curl iproute2 >/dev/null
      && while true; do
        IP=$$(ip route get 1.1.1.1 | awk '{print $$7; exit}');
        echo \\"Updating DuckDNS: $${DUCKDNS_SUBDOMAIN} -> $$IP\\";
        curl -fsS \\"https://www.duckdns.org/update?domains=$${DUCKDNS_SUBDOMAIN}&token=$${DUCKDNS_TOKEN}&ip=$$IP&verbose=true\\";
        echo;
        sleep $${UPDATE_INTERVAL_SECONDS};
      done"
"""
        
        # Create directory for DuckDNS compose file
        duckdns_dir = '/opt/chronovault/compose/duckdns'
        os.makedirs(duckdns_dir, exist_ok=True)
        
        with open(f'{duckdns_dir}/docker-compose.yml', 'w') as f:
            f.write(compose_content)
        
        # Start DuckDNS
        self.runner.run(['docker', 'compose', 'up', '-d'], cwd=duckdns_dir, timeout=120)
        
        # Wait and verify
        import time
        time.sleep(5)
        
        returncode, stdout, _ = self.runner.run(['docker', 'ps'], timeout=10)
        if 'duckdns' in stdout:
            self.log.success("DuckDNS container started")
            self.log.info("Checking DNS resolution (this may take a minute)...")
            time.sleep(10)
            
            # Try to resolve DNS using Python's socket library (more reliable than nslookup)
            try:
                hostname = f'{subdomain}.duckdns.org'
                socket.gethostbyname(hostname)
                self.log.success("DuckDNS is working!")
            except socket.gaierror:
                # DNS not resolved yet, but that's okay - it may take time
                self.log.warning("DNS may not have propagated yet. Check logs: docker logs duckdns")
            except Exception as e:
                self.log.warning(f"Could not verify DNS resolution: {e}. Check logs: docker logs duckdns")
        else:
            self.log.error("DuckDNS container failed to start")
            return False
        
        return True
