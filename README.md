# magisk-boot-patcher

Produces a Magisk-patched `boot.img` (or `init_boot.img`) **without a phone**: you give it an Android ROM zip, the tool extracts the boot image, infers from the ROM itself the settings the Magisk app would normally guess on the device, runs Magisk's official patch script and returns an image ready to flash.

Everything runs in a Docker image (amd64 and arm64): nothing else to install.

## Usage

The published image is `ghcr.io/0xsharkboy/magisk-boot-patcher`. To build it locally:

```sh
docker build -t magisk-boot-patcher .
```

In the examples below, replace `magisk-boot-patcher` with `ghcr.io/0xsharkboy/magisk-boot-patcher` to use the published image.

The current directory is mounted on `/work` (the container works there) and a named Docker volume `mbp-cache` serves as the cache for downloaded Magisk APKs.

`--magisk latest` (the default) and `canary` query the GitHub API on every run. Without authentication, it is limited to 60 requests per hour per IP address (a limit quickly reached behind a shared IP, such as GitHub Actions runners); beyond that, GitHub answers HTTP 403 and the tool falls back to the cache, or fails if the cache is empty. For heavy use, pass a token: `export GITHUB_TOKEN=…` then add `-e GITHUB_TOKEN` to `docker run` (the token is only sent to `api.github.com`).

### `inspect`: see the detected settings without patching anything

```sh
docker run --rm -v "$PWD:/work" -v mbp-cache:/cache \
  magisk-boot-patcher inspect rom.zip
```

Shows, for each setting, the value, its source (file, line or property) and the reasoning, along with the warnings. `--json` outputs the same report as JSON.

### `patch`: produce the patched boot image

```sh
docker run --rm -v "$PWD:/work" -v mbp-cache:/cache \
  magisk-boot-patcher patch rom.zip
```

Files are written to `out/` (relative to the current directory). The input can also be a bare `boot.img` or `init_boot.img`; see "Formats".

### Options

Common to both commands:

| Option | Purpose |
|---|---|
| `--debug` | show the full error traceback |
| `--version` | show the tool version (before the command) |
| `--keep-verity` / `--no-keep-verity` | force `KEEPVERITY` |
| `--keep-force-encrypt` / `--no-keep-force-encrypt` | force `KEEPFORCEENCRYPT` |
| `--patch-vbmeta-flag` / `--no-patch-vbmeta-flag` | force `PATCHVBMETAFLAG` |
| `--legacy-sar` / `--no-legacy-sar` | force `LEGACYSAR` |
| `--recovery-mode` / `--no-recovery-mode` | force `RECOVERYMODE` (never detected, see below) |
| `--preinit-device NAME` | force `PREINITDEVICE` (e.g. `cache`) |
| `--abi ABI` | device ABI (e.g. `arm64-v8a`) |

`inspect` only: `--json`.

`patch` only:

| Option | Purpose |
|---|---|
| `--magisk SPEC` | `latest` (default), `canary`, a specific version/tag, or the path of a local `.apk` |
| `--out DIR` | output directory (default: `out`) |
| `--repack-zip` | also rebuild the ROM zip with the patched boot image (see the warning below) |

A forced setting always overrides detection; its source is then shown as forced in the output and the report.

### Output files

For a ROM containing `boot.img` and Magisk v30.7, `out/` contains:

| File | Content |
|---|---|
| `boot-magisk-v30.7.img` | the patched image |
| `boot-magisk-v30.7.report.txt` | human-readable report |
| `boot-magisk-v30.7.report.json` | same report, structured |
| `<zip-name>-magisk-v30.7-unsigned.zip` | only with `--repack-zip` |

If the ROM contains an `init_boot.img`, that is the image that gets patched (as the Magisk app does) and the files are named `init_boot-magisk-v<version>.*`. For a bare image, the prefix is `boot` or `init_boot` depending on the input file name.

The report gives the Magisk version (name and code, SHA-256 of the APK), the settings used with their source, the extracted partitions, the warnings, and the SHA-256 of the original and patched images.

### Warning: the repacked zip is unsigned

The zip produced by `--repack-zip` **is not re-signed**. Its name ends with `-unsigned.zip` and the tool prints a warning: installing it requires a recovery that does not verify the signature. Flashing the patched image directly (`fastboot`, `heimdall`…) remains the normal use.

`--repack-zip` recompresses every entry of the zip: on a ROM of several GB, this can take a few minutes.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | usage error: invalid input, unsupported format, unsupported Magisk, failed integrity check… |
| 2 | internal error or external tool failure |

## Magisk settings

The Magisk app guesses them from the device; here they are inferred from the ROM. Any value that cannot be found yields a **warning** (output, `inspect`, report): the tool never guesses silently.

| Setting | Meaning | Detection |
|---|---|---|
| `KEEPVERITY` | keep dm-verity (do not disable it in the fstab) | true for a system-as-root ROM (any modern ROM, based on the SDK) |
| `KEEPFORCEENCRYPT` | keep the forced encryption of `/data` | true if the fstab's `/data` entry is encrypted: `keydirectory=` or `metadata_encryption=` (metadata encryption), `fileencryption=` (file-based), `forceencrypt=`, `encryptable=` or `forcefdeorfbe=` (block-based); without an fstab, true by default (Magisk's safe default), with a warning |
| `PATCHVBMETAFLAG` | patch the vbmeta flag in the boot image header | false if the ROM ships `vbmeta.img`; true otherwise |
| `LEGACYSAR` | hexpatch the kernel's `skip_initramfs` into `want_initramfs` | true only for an A-only system-as-root device without dynamic partitions and without a usable ramdisk in the boot image |
| `PREINITDEVICE` | storage partition Magisk uses before `/data` is mounted | same algorithm as Magisk on the `vendor` fstab: candidates `/persist`, `/metadata`, `/klogdump`, `/cache`, `/data` (the latter only if it is not encrypted, or file-based encrypted without `keydirectory=` or `metadata_encryption=`), ext4 or f2fs, under `by-name/` or `block/`, excluding logical partitions; same order of preference as Magisk. Not found: warning, because Magisk does not pick one at boot (force it with `--preinit-device`) |

On top of these come the device ABI (read from the `ro.vendor.product.cpu.abilist64` / `abilist` properties of the vendor `build.prop`), needed to pick the binaries embedded in the boot image, and `RECOVERYMODE`, always `false`: Magisk is installed in the boot image, not in recovery. The latter can be forced but is not detected.

For a bare boot image, the ROM is not available: settings not given on the command line take safe defaults, along with warnings.

## Formats

Supported:

- **block OTA** ROM zips containing `boot.img` (or `init_boot.img`), with `vendor.new.dat` + `vendor.transfer.list` or `vendor.new.dat.br` (brotli);
- `vendor` partition in **EROFS** or **ext4** (possibly as an Android sparse image);
- a bare `boot.img` or `init_boot.img`, with its settings given by hand if needed.

Not supported (rejected with a clear message, or out of scope):

- A/B ROMs in `payload.bin` format;
- `vendor_boot`;
- Magisk older than v28 (`MAGISK_VER_CODE < 28000`); if a newer APK changes its layout (missing file, new variable in `boot_patch.sh`), the tool fails rather than attempt the patch;
- KernelSU, APatch and other root solutions;
- flashing the device, signing the zip.

## How it works

1. Extracts only what is needed from the zip: the boot image, the metadata files and the `vendor` partition (never system, system_ext, product or odm).
2. Detects the settings (`vendor` fstab and properties, `vbmeta.img`, dynamic partitions, SDK).
3. Downloads the requested Magisk APK (SHA-256 checked against the GitHub asset's `digest`; a mismatch is an error) and caches it in `/cache`.
4. Runs the APK's **official** `boot_patch.sh`, unmodified, driven by environment variables, with `magiskboot` and `busybox` for the host ABI, and `magiskinit`, `magisk`, `init-ld` for the device ABI.
5. Checks that the result is indeed recognized as patched by Magisk, then writes the image, the report and, if requested, the zip.

## Tests

```sh
docker build --target dev -t mbp-dev .
docker run --rm -v "$PWD:/src" -w /src mbp-dev pytest -q
```

Two pytest markers flag special tests:

- `network`: needs network access (GitHub API, APK download), except with `MBP_TEST_APK` (see below);
- `reference`: needs a real ROM, skipped if `MBP_REFERENCE_ROM` is not set:

```sh
docker run --rm -v "$PWD:/src" -w /src \
  -v "$HOME/Downloads:/roms:ro" -e MBP_REFERENCE_ROM=/roms/my-rom.zip \
  mbp-dev pytest -q
```

To exclude a group: `pytest -q -m "not network"`.

The tests that actually patch a boot image need a Magisk APK. By default they download the latest release, once per session, and are **skipped** (not failed) if GitHub is unreachable or the API rate limit is reached (passing `-e GITHUB_TOKEN` avoids that). To run them offline, point `MBP_TEST_APK` at a local APK:

```sh
docker run --rm -v "$PWD:/src" -w /src \
  -v "$HOME/Downloads/Magisk-v30.7.apk:/apk/Magisk.apk:ro" -e MBP_TEST_APK=/apk/Magisk.apk \
  mbp-dev pytest -q
```

## Publishing and GitHub Action

- `.github/workflows/docker-publish.yml` builds the multi-arch image (`linux/amd64`, `linux/arm64`, `runtime` target) and publishes it to `ghcr.io` on every `v*` tag and every push to the default branch.
- [`examples/github-action/`](examples/github-action/README.md) contains a workflow to copy into a ROM's repository to automatically attach the patched boot image and its report to every release.
