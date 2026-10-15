"""Validated Kaitai Struct to SVG rendering with optional interactivity."""

__version__ = "0.1.0"

from .routing import DefaultRouter, OrthogonalRouter, Router


def convert(path, import_paths=(), *, show_imports=False, show_nested_types=False,
            interactive=False, color_type_edges=False, router="default", hide=()):
    """Convert a KSY file to SVG using a named or custom ``router`` class."""
    from .cli import convert_file

    return convert_file(path, import_paths, show_imports=show_imports,
                        show_nested_types=show_nested_types, interactive=interactive,
                        color_type_edges=color_type_edges, router=router, hide=hide)
