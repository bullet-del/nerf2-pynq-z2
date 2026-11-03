"""Verify and publish this source release with Git and the official GitHub CLI.

Python 3.8+, standard library only. No password or token is accepted on argv.
--check is local-only; --check-tools may download the pinned official CLI but
does not authenticate, create a repository, commit, or push.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GH_VERSION = "2.103.3"
GH_ARCHIVE = "gh_2.103.3_windows_amd64.zip"
GH_SHA256 = "98d0cfa794721665d7973dd728c78aff7bd836d58f10f6d7a03568eccb0a22e0"
GH_URL = "https://github.com/cli/cli/releases/download/v2.103.3/" + GH_ARCHIVE
DESCRIPTION = "NeRF2 FPGA inference on PYNQ-Z2, reproducible source and reusable PYNQ skills."


class PublishError(RuntimeError):
    pass


def run(args, capture=True, check=True, cwd=None):
    env = os.environ.copy()
    env["GH_HOST"] = "github.com"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # This tool never invokes a shell or logs authentication responses.
    result = subprocess.run([str(a) for a in args], cwd=cwd or ROOT, env=env,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None,
                            encoding="utf-8", errors="replace")
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise PublishError(detail)
    return result


def validate_name(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name) or name.endswith(".git"):
        raise PublishError("Use a repository name such as nerf2-pynq-z2; do not pass a URL.")
    return name


def remote_identity(url):
    # Credentials, custom hosts, options and local filesystem remotes are refused.
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?", url)
    if not match:
        raise PublishError("Origin must be a credential-free HTTPS github.com repository URL.")
    return match.group(1).lower(), match.group(2).lower()


def remote_action(info, existing_origin, owner, name, visibility):
    if existing_origin and remote_identity(existing_origin) != (owner.lower(), name.lower()):
        raise PublishError("Existing origin points elsewhere. No remote was changed.")
    if info is None:
        if existing_origin:
            raise PublishError("Origin exists, but GitHub repository lookup failed. Check account/access.")
        return "create"
    if info["nameWithOwner"].lower() != (owner + "/" + name).lower():
        raise PublishError("GitHub returned an unexpected repository identity.")
    if info["visibility"].lower() != visibility:
        raise PublishError("The existing repository has different visibility; it was not changed.")
    if not existing_origin and not info["isEmpty"]:
        raise PublishError("A non-empty repository with this name already exists. Choose another --name.")
    return "push" if existing_origin else "attach-empty"


def download_cli():
    cache = ROOT / "work/tools/github-cli" / GH_VERSION
    executable = cache / "bin/gh.exe"
    archive = cache / GH_ARCHIVE
    cache.mkdir(parents=True, exist_ok=True)
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != GH_SHA256:
        print("Downloading the pinned official GitHub CLI (about 15 MB)...", flush=True)
        partial = archive.with_suffix(".partial")
        request = urllib.request.Request(GH_URL, headers={"User-Agent": "Stage39-Source-Publisher"})
        try:
            digest = hashlib.sha256()
            total = 0
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as stream:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > 100 * 1024 * 1024:
                        raise PublishError("Unexpected GitHub CLI download size.")
                    digest.update(block)
                    stream.write(block)
            if digest.hexdigest() != GH_SHA256:
                raise PublishError("GitHub CLI SHA-256 mismatch; the downloaded file was not executed.")
            partial.replace(archive)
        except Exception:
            if partial.exists():
                partial.unlink()
            raise
    # Extract one named member, avoiding arbitrary archive paths.
    with zipfile.ZipFile(archive) as package:
        data = package.read("bin/gh.exe")
    if not executable.exists() or executable.read_bytes() != data:
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(data)
    print("Official GitHub CLI archive SHA-256: PASS", flush=True)
    return str(executable)


def ensure_gh():
    installed = shutil.which("gh")
    if installed:
        return installed
    for candidate in [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "GitHub CLI/gh.exe",
                      Path.home() / "AppData/Local/Programs/GitHub CLI/gh.exe"]:
        if candidate.is_file():
            return str(candidate)
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise PublishError("Install GitHub CLI from https://cli.github.com/ for this platform, then retry.")
    return download_cli()


def local_verification():
    if not shutil.which("git"):
        raise PublishError("Git is required. Install Git for Windows from https://git-scm.com/downloads/win.")
    print("Checking source integrity...", flush=True)
    verified = run([sys.executable, "-B", ROOT / "scripts/verify_kit.py"])
    print(verified.stdout.strip(), flush=True)
    report_path = ROOT / "work/reports/github-source-audit.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-B", ROOT / "skills/fpga-ml-reproducibility-audit/scripts/audit_fpga_repro.py",
         "--root", ROOT, "--contract", ROOT / "reproducibility-contract.json", "--mode", "source",
         "--output", report_path])
    audit = json.loads(report_path.read_text(encoding="utf-8"))
    if audit["status"] != "PASS":
        raise PublishError("Source audit did not pass.")
    print("Source audit: PASS", flush=True)
    manifest = json.loads((ROOT / "PACKAGE_SHA256.json").read_text(encoding="utf-8"))
    expected = {item["path"] for item in manifest["files"]} | {"PACKAGE_SHA256.json"}
    for relative in expected:
        path = ROOT / relative
        if path.suffix.lower() == ".ps1":
            raise PublishError("PowerShell scripts are not part of this source release.")
        if path.stat().st_size >= 100 * 1024 * 1024:
            raise PublishError("A source file reaches the regular GitHub 100 MiB limit: " + relative)
    print("Local publication checks: PASS ({} files)".format(len(expected)), flush=True)
    return expected


def repo_lookup(gh, full_name):
    # GraphQL gives a typed isEmpty result. On failure, REST distinguishes a
    # missing repository from connectivity, rate-limit and permission errors.
    result = run([gh, "repo", "view", full_name, "--json", "nameWithOwner,isEmpty,url,visibility"], check=False)
    if result.returncode == 0:
        return json.loads(result.stdout)
    probe = run([gh, "api", "repos/" + full_name], check=False)
    if probe.returncode and "HTTP 404" in (probe.stderr or ""):
        return None
    raise PublishError((result.stderr or probe.stderr or "Repository lookup failed.").strip())


def init_source_git(expected, user):
    if not (ROOT / ".git").exists():
        run(["git", "init", "--initial-branch=main", ROOT])
    top = Path(run(["git", "rev-parse", "--show-toplevel"]).stdout.strip()).resolve()
    if top != ROOT.resolve():
        raise PublishError("Refusing to operate on a parent or different Git repository.")
    branch = run(["git", "symbolic-ref", "--short", "HEAD"]).stdout.strip()
    if branch != "main":
        raise PublishError("Expected local branch main; no branch was renamed.")
    candidates = run(["git", "-c", "core.quotepath=false", "ls-files", "--cached", "--others",
                      "--exclude-standard", "-z"]).stdout
    actual = set(candidates.rstrip("\0").split("\0")) if candidates else set()
    if actual != expected:
        raise PublishError("Git/source manifest disagreement: missing={}, unexpected={}".format(
            sorted(expected - actual), sorted(actual - expected)))
    # An explicit local identity applies only to this independent publication copy.
    # It uses the account that completed GitHub authentication, never a fake author.
    name = user.get("name") or user["login"]
    email = "{}+{}@users.noreply.github.com".format(user["id"], user["login"])
    run(["git", "config", "--local", "user.name", name])
    run(["git", "config", "--local", "user.email", email])
    run(["git", "add", "--all", "--", "."])
    diff = run(["git", "diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 1:
        run(["git", "commit", "-m", "Publish verified Stage39 source and competition skills"], capture=False)
    elif diff.returncode != 0:
        raise PublishError("Unable to inspect staged source changes.")
    return run(["git", "rev-parse", "HEAD"]).stdout.strip()


def configure_credentials(gh):
    # Configure this repository only; do not replace global credential helpers.
    key = "credential.https://github.com.helper"
    executable = str(Path(gh).resolve()).replace("\\", "/")
    helper = "!" + shlex.quote(executable) + " auth git-credential"
    run(["git", "config", "--local", "--replace-all", key, ""])
    run(["git", "config", "--local", "--add", key, helper])


def publish(gh, expected, name, visibility):
    status = run([gh, "auth", "status", "--hostname", "github.com"], check=False)
    if status.returncode:
        print("GitHub browser authorization is required. Follow the device-code prompts.", flush=True)
        run([gh, "auth", "login", "--hostname", "github.com", "--git-protocol", "https",
             "--web", "--scopes", "repo,workflow"], capture=False)
    user = json.loads(run([gh, "api", "user"]).stdout)
    owner = user["login"]
    full_name = owner + "/" + name
    origin = ""
    if (ROOT / ".git").exists():
        result = run(["git", "config", "--get", "remote.origin.url"], check=False)
        origin = result.stdout.strip() if result.returncode == 0 else ""
    info = repo_lookup(gh, full_name)
    action = remote_action(info, origin, owner, name, visibility)
    commit = init_source_git(expected, user)
    print("Target: https://github.com/{} ({})".format(full_name, visibility), flush=True)
    # Use the official CLI credential helper. The script never reads its token.
    configure_credentials(gh)
    if action == "create":
        run([gh, "repo", "create", full_name, "--" + visibility, "--description", DESCRIPTION,
             "--source", ROOT, "--remote", "origin"], capture=False)
    elif action == "attach-empty":
        run(["git", "remote", "add", "origin", "https://github.com/" + full_name + ".git"])
    push = run(["git", "push", "--set-upstream", "origin", "main"], check=False)
    if push.returncode:
        message = (push.stderr or push.stdout or "Push failed.").strip()
        if "workflow" in message.lower() and "scope" in message.lower():
            print("GitHub requires permission to publish the Actions workflow. Complete authorization.", flush=True)
            run([gh, "auth", "refresh", "--hostname", "github.com", "--scopes", "workflow"], capture=False)
            push = run(["git", "push", "--set-upstream", "origin", "main"], check=False)
        if push.returncode:
            raise PublishError((push.stderr or push.stdout or message).strip())
    if push.stderr:
        print(push.stderr.strip(), flush=True)
    remote_refs = run(["git", "ls-remote", "--heads", "origin", "refs/heads/main"]).stdout.split()
    if not remote_refs or remote_refs[0] != commit:
        raise PublishError("The remote main commit does not match the local source commit.")
    report = {"schema": "stage39-github-publication-v1", "status": "PUBLISHED",
              "utc": datetime.now(timezone.utc).isoformat(), "url": "https://github.com/" + full_name,
              "visibility": visibility, "branch": "main", "commit": commit,
              "source_files": len(expected), "force_push_used": False}
    output = ROOT / "work/reports/github-publication.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\nUPLOAD SUCCESS\n" + report["url"], flush=True)
    print("Check the repository Actions tab for static-validation. Physical tests remain separate.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="nerf2-pynq-z2", type=validate_name)
    parser.add_argument("--visibility", choices=["public", "private"])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Local-only preflight; no authentication or upload")
    mode.add_argument("--check-tools", action="store_true", help="Also bootstrap/verify CLI; no login or upload")
    args = parser.parse_args(argv)
    if not (args.check or args.check_tools or args.visibility):
        parser.error("Choose --visibility public or --visibility private for publication.")
    expected = local_verification()
    if args.check:
        print("CHECK COMPLETE: no authentication, Git commit, remote creation or upload was performed.")
        return
    gh = ensure_gh()
    print(run([gh, "--version"]).stdout.splitlines()[0], flush=True)
    if args.check_tools:
        print("TOOLS READY: no authentication, Git commit, remote creation or upload was performed.")
        return
    publish(gh, expected, args.name, args.visibility)


if __name__ == "__main__":
    try:
        main()
    except (PublishError, OSError, urllib.error.URLError, zipfile.BadZipFile, KeyError, ValueError) as error:
        print("\nUPLOAD STOPPED: " + str(error), file=sys.stderr)
        print("Resolve the reported issue and run the same command again. No force push is used.", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Run the same command again to resume.", file=sys.stderr)
        raise SystemExit(130)
