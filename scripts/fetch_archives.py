"""Fetch the ``archives`` source family from explicit seeds (see scripts/primary/ and 09-frozen-contracts.md §CLI).

Run as ``python -I scripts/fetch_archives.py [--metadata-only] [--resume] [--verify] [--root PATH]``.
"""

import sys
from pathlib import Path

# ``python -I`` implies -P (Python >= 3.11): the script directory is not on sys.path, so add scripts/ explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from primary import cli  # noqa: E402

if __name__ == "__main__":
    sys.exit(cli.main("archives"))
