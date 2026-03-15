"""Base class for installation steps"""

import json
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .installer import ChronovaultInstaller


class BaseStep(ABC):
    """Base class for all installation steps"""
    
    def __init__(self, installer: 'ChronovaultInstaller'):
        self.installer = installer
        self.config = installer.config
        self.state_file = installer.state_file
        self.log = installer.log
        self.runner = installer.runner
        self.prompt = installer.prompt
        self.disk_utils = installer.disk_utils
        self.version_checker = installer.version_checker
        
    @property
    @abstractmethod
    def step_number(self) -> int:
        """Return the step number"""
        pass
    
    @property
    @abstractmethod
    def step_name(self) -> str:
        """Return the step name"""
        pass
    
    def is_completed(self) -> bool:
        """Check if this step is already completed"""
        if not os.path.exists(self.state_file):
            return False
        
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
            completed_steps = state.get('completed_steps', [])
            return self.step_number in completed_steps
        except (json.JSONDecodeError, IOError):
            return False
    
    def save_state(self):
        """Mark this step as completed and save state"""
        # Try to load existing state, but handle empty/corrupted files gracefully
        state = {
            'current_step': 0,
            'completed_steps': [],
            'config': {},
            'start_time': '',
            'last_update': ''
        }
        
        if os.path.exists(self.state_file):
            try:
                # Check if file is empty
                if os.path.getsize(self.state_file) == 0:
                    self.log.warning(f"State file is empty, starting fresh")
                else:
                    with open(self.state_file, 'r') as f:
                        loaded_state = json.load(f)
                        # Merge loaded state with defaults (preserve existing data)
                        state.update(loaded_state)
                        # Ensure all required keys exist
                        if 'completed_steps' not in state:
                            state['completed_steps'] = []
                        if 'config' not in state:
                            state['config'] = {}
            except (json.JSONDecodeError, IOError, ValueError) as e:
                self.log.warning(f"State file is corrupted or unreadable: {e}. Starting fresh.")
                # State already initialized with defaults above
        
        # Update state
        state['current_step'] = self.step_number
        if self.step_number not in state['completed_steps']:
            state['completed_steps'].append(self.step_number)
        
        # Update config
        state['config'].update(self.config)
        
        # Update timestamps
        from datetime import datetime
        if not state.get('start_time'):
            state['start_time'] = datetime.now().isoformat()
        state['last_update'] = datetime.now().isoformat()
        
        # Save to file
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def run(self) -> bool:
        """Run this step if not already completed"""
        if self.is_completed():
            self.log.info(f"Step {self.step_number} already completed, skipping...")
            return True
        
        self.log.step(self.step_number, self.step_name)
        try:
            result = self.execute()
            if result:
                self.save_state()
                self.log.success(f"Step {self.step_number} completed")
            return result
        except Exception as e:
            self.log.error(f"Step {self.step_number} failed: {str(e)}")
            raise
    
    @abstractmethod
    def execute(self) -> bool:
        """Execute the step. Return True on success, False on failure."""
        pass
