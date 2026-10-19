"""pawtrace: a modular pipeline for rebuilding and checking published computational research."""
__version__ = "0.1.0"

from .data import PanelData, Series  # noqa: E402

__all__ = ["PanelData", "Series", "__version__"]
