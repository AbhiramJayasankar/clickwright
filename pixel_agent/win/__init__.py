"""Windows backend. Importing this package makes the process DPI aware first,
so every later screen and input call works in physical pixels."""
from .dpi import ensure_dpi_aware

DPI_MODE = ensure_dpi_aware()
