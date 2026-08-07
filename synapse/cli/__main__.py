"""Run the unified CLI: ``python -m synapse.cli``."""

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
