"""Version checking utilities"""

import json
import urllib.request
from typing import Optional
from .logging import Logger
from .subprocess import SubprocessRunner


class VersionChecker:
    """Check for latest versions of software"""
    
    def __init__(self, logger: Logger):
        self.log = logger
        self.runner = SubprocessRunner(logger)
    
    def get_latest_immich_version(self) -> str:
        """Get the latest Immich version from GitHub"""
        self.log.info("Checking latest Immich version...")
        
        try:
            url = "https://api.github.com/repos/immich-app/immich/releases/latest"
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read())
                version = data.get('tag_name', '').lstrip('v')
                
                if version:
                    self.log.success(f"Latest Immich version: {version}")
                    return version
        except Exception as e:
            self.log.warning(f"Could not fetch latest Immich version: {e}")
        
        # Fallback to a known stable version
        version = "v1.120.0"
        self.log.warning(f"Using default Immich version: {version}")
        return version
    
    def get_latest_nextcloud_version(self) -> str:
        """Get the latest Nextcloud version from Docker Hub"""
        self.log.info("Checking latest Nextcloud version...")
        
        try:
            url = "https://hub.docker.com/v2/repositories/library/nextcloud/tags?page_size=100"
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read())
                
                # Find latest version matching pattern X-apache
                versions = []
                for result in data.get('results', []):
                    name = result.get('name', '')
                    if name and name.endswith('-apache') and name.replace('-apache', '').replace('.', '').isdigit():
                        versions.append(name)
                
                if versions:
                    # Sort versions (simple string sort should work for X-apache format)
                    versions.sort(reverse=True)
                    version = versions[0]
                    self.log.success(f"Latest Nextcloud version: {version}")
                    return version
        except Exception as e:
            self.log.warning(f"Could not fetch latest Nextcloud version: {e}")
        
        # Fallback to a known stable version
        version = "32-apache"
        self.log.warning(f"Using default Nextcloud version: {version}")
        return version
    
    def get_latest_twingate_version(self) -> str:
        """Get the latest Twingate connector version"""
        self.log.info("Checking latest Twingate connector version...")
        # Twingate connector typically uses version 1
        version = "1"
        self.log.success(f"Using Twingate connector version: {version}")
        return version
    
    def get_latest_watchtower_version(self) -> str:
        """Get the latest Watchtower version from Docker Hub"""
        self.log.info("Checking latest Watchtower version...")
        
        try:
            url = "https://hub.docker.com/v2/repositories/containrrr/watchtower/tags?page_size=100"
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read())
                
                # Find latest semantic version tag (e.g., 1.7.1)
                versions = []
                for result in data.get('results', []):
                    name = result.get('name', '')
                    # Match semantic version pattern (e.g., 1.7.1, 1.8.0)
                    if name and '.' in name and name.replace('.', '').replace('-', '').isdigit():
                        # Skip pre-release versions
                        if '-' not in name:
                            versions.append(name)
                
                if versions:
                    # Sort versions by converting to tuple of integers
                    def version_key(v):
                        try:
                            return tuple(map(int, v.split('.')))
                        except ValueError:
                            return (0, 0, 0)
                    
                    versions.sort(key=version_key, reverse=True)
                    version = versions[0]
                    self.log.success(f"Latest Watchtower version: {version}")
                    return version
        except Exception as e:
            self.log.warning(f"Could not fetch latest Watchtower version: {e}")
        
        # Fallback to a known stable version
        version = "1.7.1"
        self.log.warning(f"Using default Watchtower version: {version}")
        return version
    
    def get_latest_docker_api_version(self) -> str:
        """Get the latest Docker API version"""
        self.log.info("Checking Docker API version...")
        
        try:
            # Try to get API version from Docker daemon
            returncode, stdout, _ = self.runner.run(
                ['docker', 'version', '--format', '{{.Server.APIVersion}}'],
                timeout=10
            )
            
            if returncode == 0 and stdout.strip():
                version = stdout.strip()
                self.log.success(f"Docker API version: {version}")
                return version
        except Exception as e:
            self.log.warning(f"Could not query Docker API version: {e}")
        
        # Fallback to a known stable version
        version = "1.44"
        self.log.warning(f"Using default Docker API version: {version}")
        return version
