"""Step 13: Setup Twingate"""

import os
from installer.base import BaseStep


class Step13SetupTwingate(BaseStep):
    """Step 13: Setup Twingate"""
    
    @property
    def step_number(self) -> int:
        return 13
    
    @property
    def step_name(self) -> str:
        return "Setup Twingate"
    
    def execute(self) -> bool:
        """Setup Twingate connector"""
        self.log.info("Twingate Setup Instructions:")
        self.log.info("1. Go to https://www.twingate.com/")
        self.log.info("2. Sign up or log in to your account")
        self.log.info("3. Create a new network (or use existing)")
        self.log.info("4. Add a new Connector")
        self.log.info("5. Copy the setup token")
        print()
        
        input("Press Enter when you have your Twingate credentials ready...")
        
        network = self.prompt.prompt("Enter Twingate network (tenant slug)")
        access_token = self.prompt.prompt("Enter Twingate Access Token", is_password=True)
        refresh_token = self.prompt.prompt("Enter Twingate Refresh Token", is_password=True)
        
        # Validate tokens (basic check - should not contain newlines)
        access_token = access_token.strip().replace('\n', '').replace('\r', '')
        refresh_token = refresh_token.strip().replace('\n', '').replace('\r', '')
        
        if not access_token or not refresh_token:
            self.log.error("Access token or refresh token is empty")
            return False
        
        self.config['TWINGATE_NETWORK'] = network
        self.config['TWINGATE_ACCESS_TOKEN'] = access_token
        self.config['TWINGATE_REFRESH_TOKEN'] = refresh_token
        
        # Create env file - EXACT from guide, only tokens changed
        env_content = f"""TWINGATE_NETWORK={network}
TWINGATE_ACCESS_TOKEN={access_token}
TWINGATE_REFRESH_TOKEN={refresh_token}
"""
        
        with open('/opt/chronovault/env/twingate.env', 'w') as f:
            f.write(env_content)
        os.chmod('/opt/chronovault/env/twingate.env', 0o600)
        
        # Create compose file - EXACT from guide, version hardcoded to 1 as per guide
        compose_content = """services:
  twingate-connector:
    image: twingate/connector:1
    container_name: twingate-connector
    restart: unless-stopped
    env_file:
      - /opt/chronovault/env/twingate.env
    sysctls:
      net.ipv4.ping_group_range: "0 2147483647"
    environment:
      - TWINGATE_LABEL_HOSTNAME=chronovault
      - TWINGATE_LABEL_DEPLOYED_BY=docker
"""
        
        # Create directory for Twingate compose file
        twingate_dir = '/opt/chronovault/compose/twingate'
        os.makedirs(twingate_dir, exist_ok=True)
        
        with open(f'{twingate_dir}/docker-compose.yml', 'w') as f:
            f.write(compose_content)
        
        # Start Twingate
        self.runner.run(['docker', 'compose', 'up', '-d'], cwd=twingate_dir, timeout=120)
        
        # Wait and verify
        import time
        time.sleep(10)
        
        returncode, stdout, _ = self.runner.run(['docker', 'ps'], timeout=10)
        if 'twingate-connector' in stdout:
            self.log.success("Twingate connector started")
            self.log.info("Check logs: docker logs twingate-connector")
            print()
            self.log.info("Next steps:")
            subdomain = self.config.get('DUCKDNS_SUBDOMAIN', 'chronovault')
            print(f"  1. In Twingate Admin Console, create Resource:")
            print(f"     - Resource: {subdomain}.duckdns.org")
            print(f"     - Protocol: TCP")
            print(f"     - Port: 2283 (for Immich)")
            print("  2. Assign it to your user/device")
            print("  3. Install Twingate client on your devices")
        else:
            self.log.error("Twingate connector failed to start")
            return False
        
        return True
