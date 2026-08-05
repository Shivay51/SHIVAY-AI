"""Backward-compatible performance facade.

The canonical analytics and storage implementation lives in
``performance_report.py``.  This module preserves the original ``get_report``
import used by commands and startup without maintaining a second calculation
engine.
"""
from __future__ import annotations

from performance_report import get_report

__all__ = ["get_report"]
