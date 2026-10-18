"""CLI entrypoint module for Project AEGIS."""
from __future__ import annotations

import sys
from aegis.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
