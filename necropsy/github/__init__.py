"""GitHub access layer. Repository identity parsing lives here; all I/O is in
`client.py` and `endpoints.py` (design.md section 2.2)."""

from necropsy.github.urls import GITHUB_HOSTS, canonicalize

__all__ = ["GITHUB_HOSTS", "canonicalize"]
