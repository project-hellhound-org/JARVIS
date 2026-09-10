#!/usr/bin/env python3
"""
Backward-compatibility wrapper for soldierboy CLI/launcher.
Delegates directly to jarvis.py.
"""
import sys
from jarvis import main

if __name__ == "__main__":
    main()
