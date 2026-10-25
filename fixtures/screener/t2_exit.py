#!/usr/bin/env python3
"""A T2 fixture (F5): exits 3 and says why on stderr."""
import sys

sys.stderr.write("boom reason")
sys.exit(3)
