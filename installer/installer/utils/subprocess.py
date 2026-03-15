"""Safe subprocess execution utilities"""

import subprocess
import sys
from typing import Optional, List, Tuple
from .logging import Logger


class SubprocessRunner:
    """Safe subprocess execution with timeout and error handling"""
    
    def __init__(self, logger: Logger):
        self.log = logger
    
    def run(self, cmd: List[str], timeout: Optional[int] = 60,
            input_data: Optional[bytes] = None, check: bool = False,
            capture_output: bool = True, stdin_devnull: bool = False,
            cwd: Optional[str] = None) -> Tuple[int, str, str]:
        """
        Run a command safely with timeout
        
        Args:
            cmd: Command and arguments as list
            timeout: Timeout in seconds (None for no timeout)
            input_data: Input data to send to stdin
            check: If True, raise exception on non-zero exit
            capture_output: If True, capture stdout/stderr
            stdin_devnull: If True, redirect stdin to /dev/null
            cwd: Working directory for the command
        
        Returns:
            Tuple of (returncode, stdout, stderr)
        """
        # When using input parameter, don't set stdin (input handles it automatically)
        # When using stdin parameter, don't use input
        stdin = None
        if stdin_devnull:
            stdin = subprocess.DEVNULL
        # Note: When input_data is provided, we use input= parameter, not stdin=
        # subprocess.run() will automatically set up stdin as PIPE when input is used
        
        try:
            # Build keyword arguments for subprocess.run()
            # Note: cmd is passed as first positional argument, not as keyword
            run_kwargs = {
                'stdout': subprocess.PIPE if capture_output else sys.stdout,
                'stderr': subprocess.PIPE if capture_output else sys.stderr,
                'timeout': timeout,
                'check': False
            }
            
            if cwd is not None:
                run_kwargs['cwd'] = cwd
            
            if input_data:
                # Use input parameter (automatically sets up stdin as PIPE)
                run_kwargs['input'] = input_data
            elif stdin is not None:
                # Only set stdin if we're not using input
                run_kwargs['stdin'] = stdin
            
            # Pass cmd as first positional argument, kwargs as keyword arguments
            result = subprocess.run(cmd, **run_kwargs)
            
            stdout = result.stdout.decode('utf-8', errors='replace') if capture_output else ""
            stderr = result.stderr.decode('utf-8', errors='replace') if capture_output else ""
            
            if check and result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, cmd, stdout, stderr
                )
            
            return (result.returncode, stdout, stderr)
        
        except subprocess.TimeoutExpired as e:
            self.log.error(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")
            raise
        except Exception as e:
            self.log.error(f"Error running command {' '.join(cmd)}: {str(e)}")
            raise
    
    def run_success(self, cmd: List[str], timeout: Optional[int] = 60,
                   input_data: Optional[bytes] = None,
                   stdin_devnull: bool = False) -> Tuple[str, str]:
        """
        Run a command and return stdout/stderr, raising on failure
        
        Returns:
            Tuple of (stdout, stderr)
        """
        returncode, stdout, stderr = self.run(
            cmd, timeout=timeout, input_data=input_data,
            check=True, stdin_devnull=stdin_devnull
        )
        return (stdout, stderr)
    
    def check_command(self, cmd: str) -> bool:
        """Check if a command exists in PATH"""
        try:
            result = subprocess.run(
                ['which', cmd] if sys.platform != 'win32' else ['where', cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
