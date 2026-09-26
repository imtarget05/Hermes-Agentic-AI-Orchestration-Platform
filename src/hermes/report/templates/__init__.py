"""Template registry — maps report types to template classes."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import NormalizedReport as NormalizedReport

from .base import BaseTemplate

_REGISTRY: dict[str, type[BaseTemplate]] = {}


def register_template(name: str):
    """Decorator to register a template class."""
    def decorator(cls: type[BaseTemplate]):
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_template(report_type: str) -> BaseTemplate:
    """Get template instance for report type."""
    if report_type not in _REGISTRY:
        # Lazy import to avoid circular imports; importing the module is what
        # runs the @register_template decorator, so the names are unused here.
        from . import (  # noqa: F401
            financial,
            investigation,
            maintenance,
            procurement,
            research,
            workflow,
        )
        # After import, registry should be populated

    cls = _REGISTRY.get(report_type)
    if cls is None:
        raise ValueError(f"Unknown report type: {report_type}. "
                        f"Available: {list(_REGISTRY.keys())}")
    return cls()


def list_templates() -> list[str]:
    """Return list of available template names."""
    # Ensure all templates are imported (side-effect import — see get_template)
    from . import (  # noqa: F401
        financial,
        investigation,
        maintenance,
        procurement,
        research,
        workflow,
    )
    return list(_REGISTRY.keys())
