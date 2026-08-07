"""Install the optional F&O status notifier without changing trade logic."""
from __future__ import annotations

import builtins

_original_import = builtins.__import__
_installed = False


def _install(module):
    global _installed
    if _installed or not hasattr(module, "_scan_job"):
        return
    original = module._scan_job

    async def _scan_job_with_status(app):
        delivered = await original(app)
        try:
            from fno_status import publish_if_due
            await publish_if_due(app, delivered)
        except Exception:
            module.LOGGER.exception("F&O status notifier recovered from an error")
        return delivered

    module._scan_job = _scan_job_with_status
    _installed = True


def _import(name, globals=None, locals=None, fromlist=(), level=0):
    module = _original_import(name, globals, locals, fromlist, level)
    if name == "scheduler":
        _install(module)
    return module


builtins.__import__ = _import
