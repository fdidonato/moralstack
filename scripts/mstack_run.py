"""Compatibility wrapper: forwards to moralstack.cli.run.

Usage:
    python scripts/mstack_run.py [options]
"""

from moralstack.cli.run import main

if __name__ == "__main__":
    import sys

    sys.exit(main())
