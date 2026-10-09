"""Identity and authorization boundaries.

The account primitives are intentionally inert until every resource boundary
and worker path is tenant-safe. Enabling login earlier would create a false
security boundary around otherwise unprotected report APIs.
"""

MULTIUSER_READY = True
