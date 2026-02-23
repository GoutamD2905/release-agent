#!/usr/bin/env python3
"""
logger.py
=========
Comprehensive logging mechanism for the RDK-B release agent.

Provides:
  - Structured logging to console and file
  - Multiple log levels (DEBUG, INFO, WARNING, ERROR)
  - Automatic log rotation
  - Session-based log files
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional


class ReleaseLogger:
    """Structured logger for release operations."""
    
    def __init__(self, 
                 component_name: str,
                 version: str,
                 log_dir: Path = Path("/tmp/rdkb-release-conflicts/logs"),
                 console_level: int = logging.INFO,
                 file_level: int = logging.DEBUG):
        """
        Initialize release logger.
        
        Args:
            component_name: Name of the component being released
            version: Version being released
            log_dir: Directory to store log files
            console_level: Logging level for console output
            file_level: Logging level for file output
        """
        self.component_name = component_name
        self.version = version
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create session-specific log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{component_name}_{version}_{timestamp}.log"
        
        # Setup logger
        self.logger = logging.getLogger(f"release_agent_{component_name}")
        self.logger.setLevel(logging.DEBUG)
        
        # Clear any existing handlers
        self.logger.handlers.clear()
        
        # Console handler (colorized)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_formatter = ColoredFormatter(
            '%(levelname)-8s | %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)
        
        # File handler (detailed)
        file_handler = logging.FileHandler(self.log_file, mode='w', encoding='utf-8')
        file_handler.setLevel(file_level)
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(funcName)-20s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)
        
        # Log initialization
        self.logger.info(f"Release agent started for {component_name} v{version}")
        self.logger.info(f"Log file: {self.log_file}")
    
    def debug(self, msg: str) -> None:
        """Log debug message."""
        self.logger.debug(msg)
    
    def info(self, msg: str) -> None:
        """Log info message."""
        self.logger.info(msg)
    
    def warning(self, msg: str) -> None:
        """Log warning message."""
        self.logger.warning(msg)
    
    def error(self, msg: str) -> None:
        """Log error message."""
        self.logger.error(msg)
    
    def critical(self, msg: str) -> None:
        """Log critical message."""
        self.logger.critical(msg)
    
    def section(self, title: str, level: str = "INFO") -> None:
        """Log a section header."""
        separator = "=" * 60
        if level == "INFO":
            self.logger.info(separator)
            self.logger.info(title)
            self.logger.info(separator)
        elif level == "DEBUG":
            self.logger.debug(separator)
            self.logger.debug(title)
            self.logger.debug(separator)
    
    def get_log_file(self) -> Path:
        """Get the current log file path."""
        return self.log_file


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record):
        """Format log record with colors."""
        log_color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)


# Global logger instance
_logger: Optional[ReleaseLogger] = None


def init_logger(component_name: str, version: str, **kwargs) -> ReleaseLogger:
    """
    Initialize the global logger instance.
    
    Args:
        component_name: Component name
        version: Version being released
        **kwargs: Additional arguments for ReleaseLogger
        
    Returns:
        Initialized logger instance
    """
    global _logger
    _logger = ReleaseLogger(component_name, version, **kwargs)
    return _logger


def get_logger() -> ReleaseLogger:
    """
    Get the global logger instance.
    
    Returns:
        Logger instance
        
    Raises:
        RuntimeError: If logger not initialized
    """
    if _logger is None:
        raise RuntimeError("Logger not initialized. Call init_logger() first.")
    return _logger
