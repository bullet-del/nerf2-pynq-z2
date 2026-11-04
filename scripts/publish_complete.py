"""Replace the current GitHub file tree and publish all verified release assets.

Run with Python 3.8+. --check is entirely local. --publish uploads to the exact
repository named in data/release_assets.json. Old commits remain recoverable.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import publish_github as github_tools
from verify_publication import ROOT, read_json, sha256, verify


def run(args, cwd=None, check=True, live=False):
    environment = os.environ.copy()
    environment.update(GH_HOST="github.com", GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([str(a) for a in args], cwd=cwd or ROOT, env=environment,
                            stdout=None if live else subprocess.PIPE,
                            stderr=None if live else subprocess.PIPE,
                            encoding="utf-8", errors="replace")
    if check and result.returncode:
        command = " ".join(str(arg) for arg in args[:3])
        detail = (result.stderr or result.stdout or "See the command output above").strip()
        raise RuntimeError(f"{command} exited with status {result.returncode}: {detail}")
    return result


def seed_fetch_cache(workspace, checkout, origin):
    """Reuse Git objects from an earlier publication checkout for the same remote."""
    for previous in sorted(workspace.glob("checkout-*"), reverse=True):
        if previous == checkout or previous.resolve().parent != workspace.resolve():
            continue
        if not (previous / ".git").is_dir():
            continue
        configured = run(["git", "config", "--local", "--get", "remote.origin.url"],
                         cwd=previous, check=False)
        if configured.returncode or configured.stdout.strip() != origin:
            continue
        head = run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=previous, check=False)
        if head.returncode:
            continue
        result = run(["git", "fetch", "--no-tags", previous.resolve().as_uri(),
                      "HEAD:refs/stage39/fetch-cache"], cwd=checkout, check=False)
        if result.returncode == 0:
            print("Reused objects from the previous local checkout.", flush=True)
            return


def fetch_main(checkout):
    command = ["git", "fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main"]
    transient_errors = ("rpc failed", "early eof", "unexpected disconnect", "connection reset",
                        "timed out", "timeout", "http 502", "http 503", "http 504")
    for attempt in range(1, 4):
        print(f"Fetching GitHub main via HTTP/1.1 ({attempt}/3)...", flush=True)
        result = run(command, cwd=checkout, check=False)
        if result.returncode == 0:
            if result.stderr.strip():
                print(result.stderr.strip(), flush=True)
            return
        detail = (result.stderr or result.stdout or "No details from Git").strip()
        if attempt == 3 or not any(error in detail.lower() for error in transient_errors):
            raise RuntimeError(f"GitHub main fetch failed on attempt {attempt}:\n{detail}")
        print(detail, file=sys.stderr, flush=True)
        time.sleep(2 * attempt)


def replace_checkout(checkout, source, files):
    """Replace only a dedicated checkout, including deletions, as one Git commit."""
    checkout = Path(checkout).resolve()
    source = Path(source).resolve()
    top = Path(run(["git", "rev-parse", "--show-toplevel"], cwd=checkout).stdout.strip()).resolve()
    if top != checkout or source == checkout or source in checkout.parents or checkout in source.parents:
        raise RuntimeError("Replacement requires a separate dedicated checkout")
    if run(["git", "status", "--porcelain"], cwd=checkout).stdout.strip():
        raise RuntimeError("Publication checkout contains local changes")
    run(["git", "rm", "-r", "--ignore-unmatch", "--", "."], cwd=checkout)
    for relative in sorted(files):
        target = (checkout / relative).resolve()
        if checkout not in target.parents or ".git" in Path(relative).parts:
            raise RuntimeError("Unsafe publication path: " + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    run(["git", "add", "--all", "--", "."], cwd=checkout)
    actual = set(run(["git", "-c", "core.quotepath=false", "ls-files", "-z"], cwd=checkout).stdout.rstrip("\0").split("\0"))
    if actual != set(files):
        raise RuntimeError("Staged file tree differs from publication manifest")
    diff = run(["git", "diff", "--cached", "--quiet"], cwd=checkout, check=False)
    if diff.returncode == 1:
        run(["git", "commit", "-m", "Replace submission with complete Stage39 engineering release under MIT"], cwd=checkout, live=True)
    elif diff.returncode != 0:
        raise RuntimeError("Unable to inspect staged publication")
    return run(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()


def verify_remote_assets(gh, repository, release, items, assets_dir):
    remote = {asset["name"]: asset for asset in release.get("assets", [])}
    for item in items:
        asset = remote.get(item["name"])
        if not asset or asset["size"] != item["bytes"]:
            raise RuntimeError("Remote release asset missing or wrong size: " + item["name"])
        digest = asset.get("digest")
        if digest:
            if digest.lower() != "sha256:" + item["sha256"]:
                raise RuntimeError("Remote release digest mismatch: " + item["name"])
        else:
            # Older GitHub responses may omit digest. Download the public asset
            # using the authenticated CLI and compare its content locally.
            with tempfile.TemporaryDirectory(prefix="remote-check-", dir=str(assets_dir.parent)) as folder:
                run([gh, "release", "download", release["tag_name"], "--repo", repository,
                     "--pattern", item["name"], "--dir", folder])
                if sha256(Path(folder) / item["name"]) != item["sha256"]:
                    raise RuntimeError("Remote asset content mismatch: " + item["name"])


def release_source_commit(release, tag_ref, commit, checkout):
    """Resolve an existing tag or the exact target of a not-yet-published draft."""
    tag_commit = tag_ref[0] if tag_ref else release.get("target_commitish", "")
    if not tag_ref and not release.get("draft"):
        raise RuntimeError("Published release tag could not be resolved")
    if tag_commit != commit:
        ancestor = run(["git", "merge-base", "--is-ancestor", tag_commit, commit], cwd=checkout, check=False)
        if ancestor.returncode:
            raise RuntimeError("Existing release target is outside the current main history")
    return tag_commit


def find_release(gh, repository, tag):
    """The tag endpoint finds published releases. List releases to find drafts."""
    endpoint = "repos/" + repository + "/releases"
    response = run([gh, "api", endpoint + "/tags/" + tag], check=False)
    if response.returncode == 0:
        return json.loads(response.stdout)
    if "HTTP 404" not in (response.stderr or ""):
        raise RuntimeError(response.stderr or "Unable to query release")
    page = 1
    while True:
        response = run([gh, "api", endpoint + "?per_page=100&page=" + str(page)])
        releases = json.loads(response.stdout)
        matches = [release for release in releases if release.get("tag_name") == tag]
        if len(matches) > 1:
            raise RuntimeError("More than one release has the requested tag. Inspect the repository releases.")
        if matches:
            return matches[0]
        if len(releases) < 100:
            return None
        page += 1


def ensure_release(gh, repository, tag, commit):
    release = find_release(gh, repository, tag)
    if release is None:
        run([gh, "release", "create", tag, "--repo", repository, "--target", commit, "--draft",
             "--title", "Stage39 complete engineering submission (2026-11-04)",
             "--notes-file", ROOT / "docs/release_notes.md"], live=True)
        release = find_release(gh, repository, tag)
    if release is None:
        raise RuntimeError("Release was created but is not visible yet. Run the same command to resume.")
    return release


def publish(assets_dir):
    metadata = read_json(ROOT / "data/release_assets.json")
    repository = metadata["repository"]
    if repository != "bullet-del/nerf2-pynq-z2":
        raise RuntimeError("Unexpected target repository")
    gh = github_tools.ensure_gh()
    if run([gh, "auth", "status", "--hostname", "github.com"], check=False).returncode:
        print("Complete GitHub browser sign-in using the displayed device code.", flush=True)
        run([gh, "auth", "login", "--hostname", "github.com", "--git-protocol", "https",
             "--web", "--scopes", "repo,workflow"], live=True)
    user = json.loads(run([gh, "api", "user"]).stdout)
    info = json.loads(run([gh, "api", "repos/" + repository]).stdout)
    if info["full_name"].lower() != repository or info["private"] or not info.get("permissions", {}).get("push"):
        raise RuntimeError("The signed-in account needs push access to the specified public repository")
    branch = info["default_branch"]
    if branch != "main":
        raise RuntimeError("The expected default branch is main")
    workspace = ROOT.parent / "publication_work"
    workspace.mkdir(exist_ok=True)
    checkout = Path(tempfile.mkdtemp(prefix="checkout-", dir=str(workspace)))
    # A repository-local helper uses the official CLI credential store. No token
    # is read, printed, put in a URL, or passed on the command line.
    run(["git", "init", "--initial-branch=main", checkout])
    configure_credentials(gh, checkout)
    origin = "https://github.com/" + repository + ".git"
    run(["git", "remote", "add", "origin", origin], cwd=checkout)
    run(["git", "config", "--local", "http.version", "HTTP/1.1"], cwd=checkout)
    seed_fetch_cache(workspace, checkout, origin)
    fetch_main(checkout)
    run(["git", "checkout", "-B", "main", "origin/main"], cwd=checkout)
    old_commit = run(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.strip()
    run(["git", "config", "--local", "user.name", user.get("name") or user["login"]], cwd=checkout)
    email = str(user["id"]) + "+" + user["login"] + "@users.noreply.github.com"
    run(["git", "config", "--local", "user.email", email], cwd=checkout)
    manifest = read_json(ROOT / "PACKAGE_SHA256.json")
    files = {item["path"] for item in manifest["files"]} | {"PACKAGE_SHA256.json"}
    commit = replace_checkout(checkout, ROOT, files)
    print("Uploading complete replacement tree...", flush=True)
    push = run(["git", "push", "origin", "main"], cwd=checkout, check=False)
    if push.returncode:
        detail = push.stderr or push.stdout or "Git push failed"
        if "workflow" in detail.lower() and "scope" in detail.lower():
            print("GitHub requests workflow access. Complete the displayed authorization.", flush=True)
            run([gh, "auth", "refresh", "--hostname", "github.com", "--scopes", "workflow"], live=True)
            push = run(["git", "push", "origin", "main"], cwd=checkout, check=False)
        if push.returncode:
            raise RuntimeError(push.stderr or push.stdout or detail)
    if push.stderr:
        print(push.stderr.strip(), flush=True)
    remote = run(["git", "ls-remote", "origin", "refs/heads/main"], cwd=checkout).stdout.split()[0]
    if remote != commit:
        raise RuntimeError("Remote main differs from the uploaded commit")
    tag = metadata["tag"]
    release = ensure_release(gh, repository, tag, commit)
    endpoint = "repos/" + repository + "/releases/" + str(release["id"])
    print("Using release " + str(release["id"]) + " (draft=" + str(release["draft"]) + ")", flush=True)
    tag_ref = run(["git", "ls-remote", "origin", "refs/tags/" + tag], cwd=checkout).stdout.split()
    tag_commit = release_source_commit(release, tag_ref, commit, checkout)
    if tag_commit != commit:
        # Documentation/source updates may keep the same checksum-pinned binary
        # release. Every remote asset is still checked against its manifest.
        print("Reusing the verified artifact release for the updated source tree.", flush=True)
    existing = {item["name"]: item for item in release.get("assets", [])}
    for item in metadata["assets"]:
        present = existing.get(item["name"])
        if present and present.get("state") == "starter" and release.get("draft"):
            run([gh, "release", "delete-asset", tag, item["name"], "--repo", repository, "--yes"])
            present = None
        if present:
            digest = present.get("digest")
            if present["size"] != item["bytes"] or (digest and digest.lower() != "sha256:" + item["sha256"]):
                raise RuntimeError("Conflicting existing release asset: " + item["name"])
            print("Already uploaded: " + item["name"], flush=True)
            continue
        print("Uploading " + item["name"] + " (" + str(round(item["bytes"] / 1048576, 1)) + " MiB)...", flush=True)
        run([gh, "release", "upload", tag, assets_dir / item["name"], "--repo", repository], live=True)
    release = json.loads(run([gh, "api", endpoint]).stdout)
    verify_remote_assets(gh, repository, release, metadata["assets"], assets_dir)
    run([gh, "release", "edit", tag, "--repo", repository, "--draft=false", "--latest"], live=True)
    result = {"status": "PUBLISHED", "repository": repository, "previous_commit": old_commit,
              "commit": commit, "release": tag, "files": len(files),
              "artifact_release_commit": tag_commit,
              "assets": len(metadata["assets"]), "force_push_used": False}
    (workspace / "publication_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("UPLOAD SUCCESS\nhttps://github.com/" + repository + "\nhttps://github.com/" + repository + "/releases/tag/" + tag, flush=True)


def configure_credentials(gh, checkout):
    import shlex
    executable = str(Path(gh).resolve()).replace("\\", "/")
    key = "credential.https://github.com.helper"
    run(["git", "config", "--local", "--replace-all", key, ""], cwd=checkout)
    run(["git", "config", "--local", "--add", key, "!" + shlex.quote(executable) + " auth git-credential"], cwd=checkout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--publish", action="store_true")
    parser.add_argument("--assets-dir", type=Path, default=ROOT.parent / "release_assets")
    args = parser.parse_args()
    verify(args.assets_dir)
    if args.publish:
        publish(args.assets_dir.resolve())


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print("UPLOAD STOPPED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
