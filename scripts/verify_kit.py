from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from source_manifest import MANIFEST, ROOT, source_files


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if not MANIFEST.is_file():
        raise FileNotFoundError(f"missing source manifest: {MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema") != "n2-stage39-source-manifest-v2":
        raise RuntimeError("source manifest schema mismatch")
    expected = {item["path"]: item for item in manifest["files"]}
    actual = {path.relative_to(ROOT).as_posix(): path for path in source_files()}
    failures: list[str] = []
    for relative, item in expected.items():
        path = Path(os.path.abspath(ROOT / relative))
        try:
            path.relative_to(ROOT)
        except ValueError:
            failures.append(f"unsafe path: {relative}")
            continue
        if relative not in actual or not path.is_file():
            failures.append(f"missing: {relative}")
        elif path.stat().st_size != int(item["bytes"]):
            failures.append(f"size mismatch: {relative}")
        elif sha256(path) != str(item["sha256"]).lower():
            failures.append(f"hash mismatch: {relative}")
    for relative in sorted(set(actual) - set(expected)):
        failures.append(f"unmanifested source file: {relative}")
    if failures:
        raise RuntimeError("Stage39 source verification failed:\n" + "\n".join(failures))
    print("STAGE39 SOURCE MANIFEST VERIFY PASS")
    print(f"files_checked = {len(expected)}")


if __name__ == "__main__":
    main()
