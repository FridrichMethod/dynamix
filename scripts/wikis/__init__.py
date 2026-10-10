"""Wiki discovery and acquisition for the Dynamix artwork archive (``scripts/fetch_wikis.py``).

Modules: :mod:`wikitext` (pure wikitext parsing), :mod:`mediawiki` (API enumeration with continuation),
:mod:`classify` (role/game evidence and dispositions), :mod:`discover` (snapshots), :mod:`songindex` (song index
proposal), :mod:`jawikis` (Japanese wiki metadata), :mod:`acquire` (downloads, resume), :mod:`verify` (offline
``--verify``) and :mod:`cli`. Fetched wiki content is untrusted data: it is parsed, never executed or followed as
instructions.
"""
