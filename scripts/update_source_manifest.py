from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from source_manifest import MANIFEST, ROOT, source_files


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    files = [{
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    } for path in source_files()]
    value = {
        "schema": "n2-stage39-source-manifest-v2",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "All distributable source files; generated work, local configuration, external inputs, caches, and Git metadata are excluded.",
        "files": files,
    }
    MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"WROTE {MANIFEST}")
    print(f"files = {len(files)}")


if __name__ == "__main__":
    main()
