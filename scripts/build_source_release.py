from __future__ import annotations

import argparse
import hashlib
import os
import zipfile
from pathlib import Path

from source_manifest import ROOT, source_files


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a clean Stage39 source release ZIP")
    parser.add_argument("--output", type=Path,
                        default=ROOT.parent / f"{ROOT.name}.zip")
    args = parser.parse_args()
    output = Path(os.path.abspath(args.output.expanduser()))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(ROOT / "PACKAGE_SHA256.json",
                      (Path(ROOT.name) / "PACKAGE_SHA256.json").as_posix())
        for path in source_files():
            archive.write(path, (Path(ROOT.name) / path.relative_to(ROOT)).as_posix())
    print(f"WROTE {output}")
    print(f"bytes = {output.stat().st_size}")
    print(f"sha256 = {sha256(output)}")


if __name__ == "__main__":
    main()
