#!/usr/bin/env python3
"""A T2 fixture (F5): writes more than 1 MB on stdout."""
import sys

sys.stdin.read()
sys.stdout.write("x" * (2 << 20))
