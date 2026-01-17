"""MeshCore Proxy - TCP proxy for MeshCore companion radios."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("meshcore-proxy")
except PackageNotFoundError:
    # Package is not installed
    __version__ = "0.0.0.dev0"
