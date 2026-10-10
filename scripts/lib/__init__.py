"""Shared library of the Dynamix artwork archive scripts (schema version 1).

Scripts run as ``python -I scripts/<name>.py`` and import ``from lib import schema, net, media, paths, manifest``;
tests put ``<repo>/scripts`` on ``sys.path`` first. Submodules are imported on demand so that, for example, a builder
that only needs :mod:`lib.schema` does not import ``requests``. See ``scripts/lib/README.md`` for the API.
"""

__all__ = ["catalog_contract", "checks", "manifest", "media", "net", "paths", "schema"]
