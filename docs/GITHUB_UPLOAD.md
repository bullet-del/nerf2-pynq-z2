# Publish the source to GitHub

## Scope

Publish the complete repository contents, preserving the directory structure.
Datasets, original model parameters, generated BIT/HWH, power captures, build
directories and machine-local configurations are intentionally excluded.
The root README should appear directly on the GitHub repository front page.
The two competition Skills are in `skill/`; additional workflow Skills are in
`skills/`. No PowerShell script is required.

## One-command publication

Requirements: Python 3.8+, Git, an Internet connection to github.com, and a
GitHub account permitted to create personal repositories. The script works
regardless of the current working directory:

```text
python PATH/TO/Stage39_GitHub_Final_20261103/scripts/publish_github.py --name nerf2-pynq-z2 --visibility public
```

Choose `--visibility private` to publish privately. Public means anyone can
read the repository. The existing LICENSE is retained; repository visibility
is separate from permission to reuse copyrighted code.

The helper performs these steps in order:

1. Verify the source manifest, audit contract, file set and individual file size.
2. Locate GitHub CLI. On Windows x64, if absent, download official CLI 2.103.3
   to ignored `work/tools/`, verify its pinned SHA-256, and extract only gh.exe.
   It does not install a machine-wide program. Other platforms require an
   independently installed GitHub CLI.
3. Ask GitHub for browser/device-code authorization when no valid CLI login
   exists. Enter the displayed code on the official GitHub page and verify
   the intended account. No password or token belongs in a script or command.
4. Check repository identity, visibility and existing content. A previously
   unbound non-empty same-name repository causes a stop; use another name.
5. Initialize this source directory on main when needed, set this repository's
   local commit identity from the authenticated GitHub account, and commit
   the complete source. Original development directories are not modified.
6. Configure the official CLI Git credential helper locally for this repository, create the personal
   repository or attach an empty matching repository, and push main.
7. Compare remote and local main commit IDs, then print UPLOAD SUCCESS and the
   repository URL. Save a credential-free receipt in ignored work/reports/.

GitHub authorization is an interactive account action. One launch command does
not remove GitHub's device-code or browser authorization requirement.

## Checks without publication

```text
python scripts/publish_github.py --check
python scripts/publish_github.py --check-tools
```

`--check` is local-only. `--check-tools` can download/verify the optional CLI,
but neither command logs in, creates a repository, commits, or uploads.

## Resume and updates

On a network interruption, repeat the same publication command. After origin
has been bound to the selected repository, the helper reuses it. It never
deletes a remote repository, changes existing visibility, rewrites history,
or uses force push. A non-fast-forward rejection requires reviewing the remote
changes; the helper does not overwrite them.

For an intentional later source change, regenerate the manifest first:

```text
python scripts/update_source_manifest.py
python scripts/publish_github.py --name nerf2-pynq-z2 --visibility public
```

Only update the manifest after reviewing intentional changes. Do not use it to
hide a failed integrity check or an unexplained source modification.

## Verify the published repository

Open the printed URL. Confirm root README.md, 01_Vivado, 02_Model_Pipeline,
03_Package_Builder, 04_PC_App, 05_PYNQ_Board, 06_Power_Measurement, docs,
skill and skills are visible. In Actions, wait for static-validation. CI
checks source integrity, publication safety, Python syntax, and offline generic
PYNQ behavior; it does not run Vivado, QAT, or the actual board.

## Optional graphical upload

GitHub Desktop can add this local source folder after Git initialization, or
create a repository from this directory if it has none. Commit all source
changes, then Publish repository. Avoid uploading only a ZIP through the web
interface, because that prevents direct source browsing and CI discovery.

## Official references

- https://cli.github.com/manual/gh_repo_create
- https://cli.github.com/manual/gh_auth_login
- https://cli.github.com/manual/gh_auth_setup-git
- https://github.com/cli/cli/releases/tag/v2.103.3
- https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github
