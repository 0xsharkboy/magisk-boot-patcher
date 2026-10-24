from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Finding(Generic[T]):
    value: T
    source: str
    reason: str
    warning: bool = False


def _env_bool(value: bool) -> str:
    return "true" if value else "false"


@dataclass
class Settings:
    keep_verity: Finding[bool]
    keep_force_encrypt: Finding[bool]
    patch_vbmeta_flag: Finding[bool]
    legacy_sar: Finding[bool]
    recovery_mode: Finding[bool]
    preinit_device: Finding[str | None]
    device_abi: Finding[str]
    sdk: Finding[int | None]

    def to_env(self) -> dict[str, str]:
        env = {
            "KEEPVERITY": _env_bool(self.keep_verity.value),
            "KEEPFORCEENCRYPT": _env_bool(self.keep_force_encrypt.value),
            "PATCHVBMETAFLAG": _env_bool(self.patch_vbmeta_flag.value),
            "RECOVERYMODE": _env_bool(self.recovery_mode.value),
            "LEGACYSAR": _env_bool(self.legacy_sar.value),
        }
        if self.preinit_device.value is not None:
            env["PREINITDEVICE"] = self.preinit_device.value
        return env

    def warnings(self) -> list[str]:
        out = []
        for name, f in vars(self).items():
            if f.warning:
                out.append(f"{name}: {f.value!r} ({f.source}) - {f.reason}")
        return out
