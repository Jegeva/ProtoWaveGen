#!/usr/bin/env python3
"""Root-level launcher: `python main.py --config examples/i2c_7bit.json ...`
is equivalent to `python -m protowavegen` or the installed `protowavegen`
console script — this just saves typing the module path."""

import sys

from protowavegen.main import main

if __name__ == "__main__":
    sys.exit(main())
