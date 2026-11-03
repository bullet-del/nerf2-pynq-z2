#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOARD = ROOT / "outputs/N2-NeRF2-Stage39-board"


def load_guard(path):
    spec = importlib.util.spec_from_file_location("stage39_import_guard_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load import guard")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, default=DEFAULT_BOARD)
    args = parser.parse_args()
    board = args.package_root.resolve()
    guard = load_guard(board / "jupyter/stage39_import_guard.py")
    expected = (board / "software/stage5/n2_stream.py").resolve()
    old_path = list(sys.path)
    old_module = sys.modules.get("n2_stream")
    try:
        with tempfile.TemporaryDirectory(prefix="stage39_wrong_n2_stream_") as directory:
            wrong = Path(directory) / "n2_stream.py"
            wrong.write_text("WRONG_MODULE = True\n", encoding="utf-8")
            sys.path.insert(0, directory)
            fake = types.ModuleType("n2_stream")
            fake.__file__ = str(wrong)
            fake.WRONG_MODULE = True
            sys.modules["n2_stream"] = fake

            module = guard.prepare_stage39_imports(board)
            assert Path(module.__file__).resolve() == expected
            assert module.P == 32 and module.OP_ECHO == 0xF
            assert all(hasattr(module, name)
                       for name in guard.REQUIRED_N2_STREAM_SYMBOLS)
            assert sys.modules["n2_stream"] is module
            assert not hasattr(module, "WRONG_MODULE")
    finally:
        sys.path[:] = old_path
        if old_module is None:
            sys.modules.pop("n2_stream", None)
        else:
            sys.modules["n2_stream"] = old_module
    print("STAGE39 DETERMINISTIC IMPORT REGRESSION PASS")


if __name__ == "__main__":
    main()
