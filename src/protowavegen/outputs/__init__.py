from .base import OutputWriter, get_output_class, register_output

# Importing each module registers its @register_output-decorated class.
from . import sigrok_writer, svg_writer, vcd_writer  # noqa: F401,E402

__all__ = ["OutputWriter", "register_output", "get_output_class"]
