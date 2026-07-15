"""wenji — generic Chinese markdown RAG framework."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("wenji")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0.dev0"
