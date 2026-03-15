"""Disk utilities for disk selection and information"""

import os
import stat
import subprocess
from typing import Optional, List, Dict, Tuple
from .logging import Logger
from .subprocess import SubprocessRunner


class DiskUtils:
    """Utilities for disk operations"""
    
    def __init__(self, logger: Logger):
        self.log = logger
        self.runner = SubprocessRunner(logger)
    
    def list_disks(self, exclude_disk: Optional[str] = None) -> List[Dict[str, str]]:
        """List available disks, excluding the specified disk"""
        disks = []
        
        # Use lsblk to get all block devices (disks only, not partitions)
        # -d: print only devices (not partitions)
        # -n: no headings
        # -o NAME,TYPE: output device names and types
        returncode, stdout, _ = self.runner.run(
            ['lsblk', '-d', '-n', '-o', 'NAME,TYPE'],
            timeout=5
        )
        
        if returncode != 0:
            return disks
        
        # Parse device names and get info for each
        for line in stdout.strip().split('\n'):
            if not line.strip():
                continue
            
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            
            device_name = parts[0]
            device_type = parts[1]
            
            # Only process actual disk devices (TYPE should be "disk")
            if device_type != 'disk':
                continue
            
            disk_path = f"/dev/{device_name}"
            
            # Skip if this is the exclude disk
            if exclude_disk:
                exclude_name = os.path.basename(exclude_disk)
                if device_name == exclude_name or disk_path == exclude_disk:
                    continue
            
            disk_info = self._get_disk_info(disk_path)
            if disk_info:
                disks.append(disk_info)
        
        return disks
    
    def _get_disk_info(self, disk_path: str) -> Optional[Dict[str, str]]:
        """Get information about a disk"""
        try:
            devname = os.path.basename(disk_path)
            
            # Get disk info using lsblk - parse each field separately
            size_bytes = 0
            model = "Unknown"
            disk_type = "disk"
            mountpoint = "(not mounted)"
            
            # Get size
            returncode, stdout, _ = self.runner.run(
                ['lsblk', '-b', '-d', '-o', 'SIZE', '-n', disk_path],
                timeout=5
            )
            if returncode == 0 and stdout.strip().isdigit():
                size_bytes = int(stdout.strip())
            
            # Get model
            returncode, stdout, _ = self.runner.run(
                ['lsblk', '-d', '-o', 'MODEL', '-n', disk_path],
                timeout=5
            )
            if returncode == 0:
                model = stdout.strip() or "Unknown"
            
            # Get type
            returncode, stdout, _ = self.runner.run(
                ['lsblk', '-d', '-o', 'TYPE', '-n', disk_path],
                timeout=5
            )
            if returncode == 0:
                disk_type = stdout.strip() or "disk"
            
            # Get mountpoint
            returncode, stdout, _ = self.runner.run(
                ['lsblk', '-d', '-o', 'MOUNTPOINT', '-n', disk_path],
                timeout=5
            )
            if returncode == 0:
                mountpoint = stdout.strip() or "(not mounted)"
            
            # Format size
            if size_bytes >= 1073741824:
                size_str = f"{size_bytes / 1073741824:.2f} GB"
            elif size_bytes >= 1048576:
                size_str = f"{size_bytes / 1048576:.2f} MB"
            else:
                size_str = f"{size_bytes / 1024:.2f} KB"
            
            return {
                'device': devname,
                'path': disk_path,
                'size': size_str,
                'model': model,
                'type': disk_type,
                'mountpoint': mountpoint
            }
        except Exception:
            return None
    
    def display_disks(self, disks: List[Dict[str, str]]):
        """Display disks in a formatted table"""
        print()
        self.log.info("Available disks:")
        print()
        print(f"{'DEVICE':<10} {'SIZE':<12} {'MODEL':<30} {'TYPE':<8} {'MOUNTPOINT':<20}")
        print("─" * 78)
        
        for disk in disks:
            print(f"{disk['device']:<10} {disk['size']:<12} {disk['model']:<30} "
                  f"{disk['type']:<8} {disk['mountpoint']:<20}")
        print()
    
    def validate_disk(self, disk_path: str) -> bool:
        """Validate that a disk exists and is a block device"""
        if not os.path.exists(disk_path):
            return False
        try:
            return stat.S_ISBLK(os.stat(disk_path).st_mode)
        except (OSError, FileNotFoundError):
            return False
    
    def get_disk_uuid(self, disk_path: str) -> Optional[str]:
        """Get the UUID of a LUKS encrypted disk"""
        try:
            returncode, stdout, _ = self.runner.run(
                ['blkid', '-s', 'UUID', '-o', 'value', disk_path],
                timeout=5
            )
            if returncode == 0:
                return stdout.strip().split('\n')[0]
        except Exception:
            pass
        return None
