#!/usr/bin/env python3
"""A T2 fixture (004 T019): prints junk and exits 1. Standard library only."""
import sys

sys.stdin.read()
print("this is not json")
sys.exit(1)
