# Replace the GitHub tree and publish complete artifacts

Run Python 3.8+ from PowerShell using the prepared publication bundle. It contains
the `repository` folder and the sibling `release_assets` folder.

```text
python repository/scripts/publish_complete.py --check
python repository/scripts/publish_complete.py --publish
```

The target is `bullet-del/nerf2-pynq-z2`. GitHub sign-in opens when required.
Use an account with write access and authorize repository/workflow access.
Git is required. GitHub CLI is located locally or downloaded from its official
release using the pinned SHA-256 in `scripts/publish_github.py`.

The script fetches the current main branch into a new independent checkout,
removes the previously tracked files there, copies the manifest-listed complete
publication, and commits the replacement as one tree. Pushing the commit replaces
the current GitHub files while retaining previous commits.

The new checkout uses HTTP/1.1 for Git and reuses local objects from an earlier
publication checkout for the same remote when available. A transient fetch
failure is retried up to three total attempts and preserves Git's error details.

The six larger artifacts are uploaded to a draft GitHub Release. The script
compares remote sizes and SHA-256 digests before publishing that release.
The root README links to each asset. If interrupted, rerun the same command.
Matching assets are reused. Conflicting existing assets cause a stop.

When main receives a documentation update, the uploader can reuse a matching
artifact release tagged at an earlier commit in the same main history.

Draft Releases are located through the authenticated release list when the tag
endpoint returns 404. Further status reads use the stable release ID, including
while the draft is displayed with an `untagged-...` URL.

The command prints `UPLOAD SUCCESS` after both the repository and Release pass.
Its receipt is written to the sibling `publication_work/publication_result.json`.
The local source directory and existing submission archive stay available.

GitHub documents its 100 MiB regular-file limit and recommends Release assets
for distributing larger binaries. The uploader uses that distribution path.
