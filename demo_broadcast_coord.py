#!/usr/bin/env python3
# Demo/test entrypoint for broadcast_coord.py (TWICE-57160).
# The repo's demo_*.sh idiom needs bash, but this worktree's shell is busybox
# only. This thin runner delegates to the in-process stdlib test, which spins
# a live mock broker + the sidecar and asserts the full 6-endpoint contract.
# Run:  python3 demo_broadcast_coord.py
import os, sys, subprocess
here = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.call([sys.executable, os.path.join(here, "test_broadcast_coord.py")]))