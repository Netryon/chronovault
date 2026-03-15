"""Step 9: Create App Data Directories"""

import os
from installer.base import BaseStep


class Step9CreateAppDirectories(BaseStep):
    """Step 9: Create App Data Directories"""
    
    @property
    def step_number(self) -> int:
        return 9
    
    @property
    def step_name(self) -> str:
        return "Create App Data Directories"
    
    def execute(self) -> bool:
        """Create application data directories"""
        # Ensure primary is mounted
        if not os.path.ismount('/mnt/primary'):
            self.log.error("Primary disk is not mounted at /mnt/primary")
            return False
        
        # Create app directories
        directories = [
            '/mnt/primary/apps',
            '/mnt/primary/apps/immich/library',
            '/mnt/primary/apps/immich/upload',
            '/mnt/primary/apps/immich/profile',
            '/mnt/primary/apps/immich/thumbs',
            '/mnt/primary/apps/immich/postgres',
            '/mnt/primary/apps/nextcloud/html',
            '/mnt/primary/apps/nextcloud/data',
            '/mnt/primary/apps/postgres/nextcloud'
        ]
        
        for directory in directories:
            os.makedirs(directory, exist_ok=True)
        
        # Get sysadmin UID/GID
        import pwd
        try:
            sysadmin_uid = pwd.getpwnam('sysadmin').pw_uid
            sysadmin_gid = pwd.getpwnam('sysadmin').pw_gid
            os.chown('/mnt/primary/apps', sysadmin_uid, sysadmin_gid)
            for root, dirs, files in os.walk('/mnt/primary/apps'):
                os.chown(root, sysadmin_uid, sysadmin_gid)
                for d in dirs:
                    os.chown(os.path.join(root, d), sysadmin_uid, sysadmin_gid)
                for f in files:
                    os.chown(os.path.join(root, f), sysadmin_uid, sysadmin_gid)
        except KeyError:
            # If sysadmin user doesn't exist, use root
            self.runner.run(['chown', '-R', 'root:root', '/mnt/primary/apps'], timeout=30)
        
        self.runner.run(['chmod', '-R', '755', '/mnt/primary/apps'], timeout=30)
        
        return True
