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
