"""Wiki discovery and acquisition for the Dynamix artwork archive (``scripts/fetch_wikis.py``).

Modules: :mod:`config` (registered sources and reviewed page tables), :mod:`wikitext` (wikitext parsing),
:mod:`mediawiki` (Action API enumeration with continuation), :mod:`classify` (role/game evidence and dispositions),
:mod:`reviews` (archivist reviews pinned to the reviewed revisions), :mod:`discover` (inventories and per-file plans),
:mod:`songindex` (song index proposal), :mod:`jawikis` (Japanese wiki song lists), :mod:`acquire` (state, journal,
lock, downloads), :mod:`outputs` (snapshots and source records), :mod:`verify` (offline ``--verify``) and :mod:`cli`.
Fetched wiki content is untrusted data: it is parsed, never executed, rendered or followed as instructions.
"""
