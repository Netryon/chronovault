"""Interactive prompt utilities"""

import getpass
import sys
from typing import Optional, Callable, Any
from .logging import Colors, Logger


class Prompter:
    """Handle user input prompts"""
    
    def __init__(self, logger: Logger):
        self.log = logger
    
    def prompt(self, prompt_text: str, default: Optional[str] = None, 
               is_password: bool = False) -> str:
        """Prompt for user input"""
        if is_password:
            return self._prompt_password(prompt_text)
        
        if default:
            prompt_text = f"{Colors.CYAN}{prompt_text}{Colors.NC} [{default}]: "
        else:
            prompt_text = f"{Colors.CYAN}{prompt_text}{Colors.NC}: "
        
        try:
            response = input(prompt_text).strip()
            return response if response else (default or "")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted by user")
            sys.exit(1)
    
    def _prompt_password(self, prompt_text: str) -> str:
        """Prompt for password (hidden input)"""
        try:
            prompt_text = f"{Colors.CYAN}{prompt_text}{Colors.NC}: "
            password = getpass.getpass(prompt_text)
            return password
        except (EOFError, KeyboardInterrupt):
            print("\nAborted by user")
            sys.exit(1)
    
    def prompt_yesno(self, prompt_text: str, default: str = "yes") -> bool:
        """Prompt for yes/no answer"""
        if default.lower() == "yes":
            prompt_text = f"{Colors.CYAN}{prompt_text}{Colors.NC} [Y/n]: "
        else:
            prompt_text = f"{Colors.CYAN}{prompt_text}{Colors.NC} [y/N]: "
        
        try:
            response = input(prompt_text).strip().lower()
            if not response:
                response = default.lower()
            
            return response in ('y', 'yes')
        except (EOFError, KeyboardInterrupt):
            print("\nAborted by user")
            sys.exit(1)
    
    def prompt_choice(self, prompt_text: str, options: list[str]) -> str:
        """Prompt for choice from a list of options"""
        print(f"{Colors.CYAN}{prompt_text}{Colors.NC}")
        for i, option in enumerate(options, 1):
            print(f"  {i}) {option}")
        
        while True:
            try:
                choice = input(f"Select option [1-{len(options)}]: ").strip()
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(options):
                        return options[idx]
                print(f"Invalid choice. Please enter a number between 1 and {len(options)}")
            except (EOFError, KeyboardInterrupt):
                print("\nAborted by user")
                sys.exit(1)
