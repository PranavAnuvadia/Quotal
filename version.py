"""Single source of truth for the Quotal release version.

Bump this on every release. The installer (installer/quotal.iss) and the
in-app OTA updater (updater.py) both read it — never hardcode the version
anywhere else.
"""

__version__ = "1.0.0"
