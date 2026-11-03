#!/usr/bin/env python3
"""Install Stage39 runtime imports deterministically from this board package."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REQUIRED_N2_STREAM_SYMBOLS = (
    "P", "OP_ECHO", "u64", "parse_tv3", "selfcheck", "enc_beats",
    "layer_beats", "start_beat",
)


def _resolved(value):
    return str(Path(value).resolve())


def prepare_stage39_imports(root):
    """Put package paths in a fixed order and load the exact protocol module.

    Jupyter kernels can retain a same-named module from an older Stage package.
    The supervised worker is a fresh process, but it still must not rely on the
    incidental order of ``sys.path``.  This function therefore loads the
    Stage39 package's Stage5 protocol file by absolute path and registers that
    verified object under the legacy module name used by the hardware drivers.
    """
    root = Path(root).resolve()
    runtime_paths = [
        root / "jupyter",
        root / "software/stage5",
        root / "software/stage8",
        root / "software/stage10",
        root / "software/stage24",
    ]
    missing = [str(path) for path in runtime_paths if not path.is_dir()]
    if missing:
        raise ImportError("Stage39 runtime path is missing: " + ", ".join(missing))

    ordered = [_resolved(path) for path in runtime_paths]
    ordered_set = set(ordered)
    sys.path[:] = ordered + [entry for entry in sys.path
                             if _resolved(entry or ".") not in ordered_set]

    expected = (root / "software/stage5/n2_stream.py").resolve()
    if not expected.is_file():
        raise ImportError("Stage39 protocol module is missing: " + str(expected))

    # A notebook may already have cached another package's n2_stream.  Replace
    # it deliberately instead of accepting an object solely by module name.
    sys.modules.pop("n2_stream", None)
    spec = importlib.util.spec_from_file_location("n2_stream", str(expected))
    if spec is None or spec.loader is None:
        raise ImportError("Cannot create Stage39 protocol import spec: " + str(expected))
    module = importlib.util.module_from_spec(spec)
    sys.modules["n2_stream"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("n2_stream", None)
        raise

    actual = Path(getattr(module, "__file__", "")).resolve()
    missing_symbols = [name for name in REQUIRED_N2_STREAM_SYMBOLS
                       if not hasattr(module, name)]
    if actual != expected or missing_symbols or int(getattr(module, "P", -1)) != 32:
        sys.modules.pop("n2_stream", None)
        raise ImportError(
            "Stage39 protocol identity mismatch: expected=%s actual=%s "
            "missing=%s P=%r" %
            (expected, actual, missing_symbols, getattr(module, "P", None)))
    return module


__all__ = ["REQUIRED_N2_STREAM_SYMBOLS", "prepare_stage39_imports"]
