#!/usr/bin/env python3
"""A T2 fixture (F5): starts a grandchild that holds the pipes, then sleeps past any timeout."""
import subprocess
import sys
import time

subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(60)
