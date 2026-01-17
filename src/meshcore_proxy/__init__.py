"""MeshCore Proxy - TCP proxy for MeshCore companion radios."""

try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:
    from importlib_metadata import version, PackageNotFoundError

try:
    __version__ = version("meshcore-proxy")
except PackageNotFoundError:
    # Package is not installed
    __version__ = "0.0.0.dev0"
