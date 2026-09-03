"""CUDA probe executed inside each isolated worker environment."""

from __future__ import annotations

import importlib
from typing import Any


def cuda_status() -> dict[str, Any]:
    """Report PyTorch wheel/driver interoperability without requiring CUDA Toolkit."""

    try:
        torch = importlib.import_module("torch")
    except ImportError as error:
        return {
            "torch_installed": False,
            "cuda_available": False,
            "detail": str(error),
            "cuda_toolkit_required": False,
        }
    cuda = getattr(torch, "cuda", None)
    available = bool(cuda is not None and cuda.is_available())
    return {
        "torch_installed": True,
        "torch_version": str(getattr(torch, "__version__", "unknown")),
        "torch_cuda_build": str(getattr(getattr(torch, "version", None), "cuda", None)),
        "cuda_available": available,
        "device_count": int(cuda.device_count()) if available and cuda is not None else 0,
        "cuda_toolkit_required": False,
    }
