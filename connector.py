#!/usr/bin/env python3
"""Entry shim. Implementation lives in the `bank_connector` package."""
import sys

from bank_connector.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]) or 0)
