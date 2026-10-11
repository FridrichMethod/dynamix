"""Primary-source acquisition (artists, official, archives families) for the Dynamix artwork archive.

The three fetch scripts (``scripts/fetch_artists.py``, ``fetch_official.py``, ``fetch_archives.py``) share one runner
(:mod:`primary.runner`) and a set of narrow per-platform adapters (:mod:`primary.adapters`). What to fetch is data:
``scripts/primary/seeds/<family>.json`` lists every source, its adapter, its explicit seeds (post IDs, page URLs,
CDX queries) and the curated classification of each expected candidate. Adapters never crawl a whole platform.
"""
