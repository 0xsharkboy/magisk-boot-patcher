# Example GitHub Action for ROM maintainers

`patch-boot.yml` is a workflow to copy into your ROM's repository, as `.github/workflows/patch-boot.yml`.

On every **published** release (`on: release: [published]`), it:

1. downloads the ROM zip attached to the release (`gh release download`);
2. runs `docker run ghcr.io/0xsharkboy/magisk-boot-patcher patch <zip>`;
3. attaches `out/*-magisk-*.img` (the patched boot image, or `init_boot`) and `out/*.report.txt` to the same release (`gh release upload --clobber`).

## What to adapt

- `ROM_ASSET_GLOB` (`env` variable at the top of the file, `*.zip` by default): pattern of the ROM zip asset. It must match **exactly one** file, otherwise the job fails. Since assets can be large, they are downloaded with `gh release download` rather than through `actions/download-artifact`.
- `MBP_IMAGE`: the tool's image. Pin a version tag rather than `latest` for reproducible builds.
- `patch` options (`--magisk`, forced settings…): add them to the `docker run` command; see the main README.

## Requirements

- `permissions: contents: write`, already declared in the workflow, so that `GITHUB_TOKEN` can upload to the release.
- A runner with Docker (`ubuntu-latest` works).
- Magisk versions older than v28 are not supported.

No extra secret is needed. The workflow only writes to the release that triggered it.

The `GITHUB_TOKEN` is also passed to the container (`-e GITHUB_TOKEN`): `--magisk latest` queries the GitHub API, whose anonymous rate limit is shared by hosted runners and quickly reached (HTTP 403, then failure if there is no cache).

## Limitations

- **2 GiB per asset**: GitHub rejects release assets larger than 2 GiB. A larger ROM cannot be attached to the release; host it elsewhere and replace the "Download the ROM zip" step with a download from that location (`curl -fLo rom/rom.zip "$URL"`, for example). The patched boot image and the report stay small.
- **Releases created by a workflow**: a release created with another workflow's `GITHUB_TOKEN` does not trigger `on: release` (GitHub does not chain workflows triggered by that token). If your releases are published by a workflow, create them with a personal token or a GitHub App, or add this workflow's steps to the one that publishes (adapting `TAG`, which comes from the `release` event here).
