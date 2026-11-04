"""Download checksum-verified deployment packages or original reproduction inputs."""
from __future__ import annotations

import argparse
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

from verify_publication import ROOT, read_json, sha256


def extract_new(archive, destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            target = (destination / entry.filename).resolve()
            if destination not in target.parents:
                raise RuntimeError("Unsafe archive path")
            if target.exists():
                raise FileExistsError("Extraction target already exists: " + str(target))
        package.extractall(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", choices=["all", "inputs", "board", "pc", "results", "power", "video"], default="all")
    parser.add_argument("--destination", type=Path, default=ROOT / "external/downloads")
    parser.add_argument("--extract-inputs", action="store_true")
    args = parser.parse_args()
    aliases = {"inputs": "nerf2_inputs.zip", "board": "board_package.zip", "pc": "pc_app_package.zip",
               "results": "test_results.zip", "power": "power_logs.zip", "video": "demonstration.mp4"}
    items = read_json(ROOT / "data/release_assets.json")["assets"]
    args.destination.mkdir(parents=True, exist_ok=True)
    for item in items:
        if args.asset != "all" and item["name"] != aliases[args.asset]:
            continue
        target = args.destination / item["name"]
        if not target.exists() or sha256(target) != item["sha256"]:
            print("Downloading " + item["name"], flush=True)
            partial = target.with_suffix(target.suffix + ".partial")
            request = urllib.request.Request(item["url"], headers={"User-Agent": "stage39-artifact-downloader"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
            if partial.stat().st_size != item["bytes"] or sha256(partial) != item["sha256"]:
                raise RuntimeError("Downloaded content failed SHA-256: " + item["name"])
            os.replace(partial, target)
        print("VERIFIED " + str(target), flush=True)
    if args.extract_inputs:
        target = args.destination / "nerf2_inputs.zip"
        expected = next(item for item in items if item["name"] == "nerf2_inputs.zip")
        if not target.is_file() or sha256(target) != expected["sha256"]:
            raise RuntimeError("Select --asset inputs or all before extracting verified inputs")
        extract_new(target, ROOT / "external")
        print("Inputs extracted to " + str(ROOT / "external/nerf2_inputs"))


if __name__ == "__main__":
    main()
