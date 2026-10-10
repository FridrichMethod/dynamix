#!/usr/bin/env python3
"""Discover, classify and acquire Dynamix artwork from the English and Japanese Dynamix wikis.

Run as ``python -I scripts/fetch_wikis.py [--metadata-only] [--resume] [--limit N] [--source NAME]
[--include-animated] [--verify] [--root PATH]``; see ``scripts/wikis/cli.py`` and docs/implementation/09-frozen-contracts.md.
Writes only ``data/sources/wikis.json``, ``data/discovery/{miraheze,fandom,atwiki-ja,rhythmgame-ja}.json``,
``data/discovery/song-index.wikis.json`` and ``assets/public/wikis/**`` under the root.
"""

import sys
from pathlib import Path

# ``python -I`` implies -P (Python >= 3.11): the script directory is not on sys.path, so add scripts/ explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from wikis.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
