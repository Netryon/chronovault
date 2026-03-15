"""Logging utilities for Chronovault Installer"""

import os
import sys
from datetime import datetime
from typing import Optional


class Colors:
    """ANSI color codes"""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    MAGENTA = '\033[0;35m'
    BOLD = '\033[1m'
    NC = '\033[0m'  # No Color


class Logger:
    """Logger for installation steps"""
    
    def __init__(self, log_file: str = "/tmp/chronovault-installer.log"):
        self.log_file = log_file
        self._init_log_file()
    
    def _init_log_file(self):
        """Initialize log file with proper permissions"""
        try:
            if os.path.exists(self.log_file):
                os.remove(self.log_file)
            with open(self.log_file, 'w') as f:
                f.write(f"# Chronovault Installer Log - {datetime.now()}\n")
            os.chmod(self.log_file, 0o644)
        except (IOError, OSError):
            # If we can't create log file, disable logging
            self.log_file = "/dev/null"
    
    def _write_log(self, level: str, message: str):
        """Write message to log file"""
        if self.log_file != "/dev/null":
            try:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with open(self.log_file, 'a') as f:
                    f.write(f"[{timestamp}] [{level}] {message}\n")
            except (IOError, OSError):
                pass  # Silently fail if we can't write to log
    
    def log(self, message: str):
        """Log a general message"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f"[{timestamp}] {message}"
        print(msg)
        self._write_log("LOG", message)
    
    def info(self, message: str):
        """Log an info message"""
        msg = f"{Colors.BLUE}[INFO]{Colors.NC} {message}"
        print(msg)
        self._write_log("INFO", message)
    
    def success(self, message: str):
        """Log a success message"""
        msg = f"{Colors.GREEN}[✓]{Colors.NC} {message}"
        print(msg)
        self._write_log("SUCCESS", message)
    
    def warning(self, message: str):
        """Log a warning message"""
        msg = f"{Colors.YELLOW}[!]{Colors.NC} {message}"
        print(msg)
        self._write_log("WARNING", message)
    
    def error(self, message: str):
        """Log an error message"""
        msg = f"{Colors.RED}[✗]{Colors.NC} {message}"
        print(msg, file=sys.stderr)
        self._write_log("ERROR", message)
    
    def step(self, step_num: int, step_name: str):
        """Log a step header"""
        print()
        print(f"{Colors.CYAN}{'━' * 78}{Colors.NC}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}Step {step_num}:{Colors.NC} {step_name}")
        print(f"{Colors.CYAN}{'━' * 78}{Colors.NC}")
        print()
        self.log(f"Starting step {step_num}: {step_name}")
