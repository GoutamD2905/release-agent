#!/usr/bin/env python3
"""
utils.py
========
Common utility functions for the RDK-B release agent scripts.

Provides:
  - ANSI color formatting
  - Console output helpers
  - Shared constants
"""

# ── ANSI Color Codes ──────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


# ── Color Helper Functions ────────────────────────────────────────────────────
def c(color: str, text: str) -> str:
    """Apply ANSI color to text."""
    return f"{color}{text}{RESET}"


def ok(msg: str) -> str:
    """Format success message with green checkmark."""
    return c(GREEN, f"✅  {msg}")


def warn(msg: str) -> str:
    """Format warning message with yellow icon."""
    return c(YELLOW, f"⚠️   {msg}")


def err(msg: str) -> str:
    """Format error message with red X."""
    return c(RED, f"❌  {msg}")


def info(msg: str) -> str:
    """Format info message with blue icon."""
    return c(CYAN, f"ℹ️   {msg}")


def dim(msg: str) -> str:
    """Format dimmed/secondary text."""
    return c(DIM, msg)


def bold(msg: str) -> str:
    """Format bold text."""
    return c(BOLD, msg)


# ── Banner and Section Helpers ────────────────────────────────────────────────
def banner(title: str, width: int = 64) -> None:
    """Print a formatted banner with title."""
    print("\n" + c(BOLD, "═" * width))
    print(c(BOLD, f"  {title}"))
    print(c(BOLD, "═" * width))


def section(step: int, title: str, start_time: float = None) -> None:
    """Print a formatted section header with optional elapsed time."""
    import time
    if start_time is not None:
        elapsed = time.time() - start_time
        print(f"\n{c(BOLD, f'[Step {step}]')} {title}  {dim(f'+{elapsed:.1f}s')}")
    else:
        print(f"\n{c(BOLD, f'[Step {step}]')} {title}")
    print(c(DIM, "  " + "─" * 56))
