"""Validated Kaitai Struct to SVG rendering with optional interactivity."""

__version__ = "0.1.0"

from .routing import DefaultRouter, OrthogonalRouter, Router
from .placement import DefaultLayout, LayoutEngine, OptimizedLayout


def convert(path, import_paths=(), *, show_imports=False, show_nested_types=False,
            interactive=False, color_type_edges=False, router="default", layout="default", hide=(),
            treeshake=False, out_dir=None):
    """Convert a KSY file to SVG using named or custom layout and router classes."""
    from .cli import convert_file

    return convert_file(path, import_paths, show_imports=show_imports,
                        show_nested_types=show_nested_types, interactive=interactive,
                        color_type_edges=color_type_edges, router=router, layout=layout, hide=hide,
                        treeshake=treeshake, out_dir=out_dir)
