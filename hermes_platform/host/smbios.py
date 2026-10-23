"""SMBIOS (DMI) system identity: who built this machine and what they call it.

    Type 1 System      Manufacturer / Product Name / Family / Version / SKU
    Type 2 Baseboard   Manufacturer / Product (often the OEM's internal model code)
    Type 3 Enclosure   Chassis type (laptop / desktop / tablet ...)

Windows reads ``HKLM\\HARDWARE\\DESCRIPTION\\System\\BIOS`` (the kernel copies SMBIOS there at
boot, so no WMI spawn); Linux reads ``/sys/class/dmi/id``; macOS has no SMBIOS and reads the
``hw.model`` sysctl. Never the environment, never a subprocess.

The OEM fields name the chassis builder, not the SoC (an N1X laptop reports ASUS, never NVIDIA;
``products.nvidia_platform`` answers that). OEMs write these strings and users can edit them:
treat them as labels for choosing defaults, never as attestation.
"""

from __future__ import annotations

import functools
import re
import sys
from collections.abc import Mapping
from typing import NamedTuple

from hermes_platform.host import facts

# Placeholder strings firmware ships when the OEM never filled the field in.
_PLACEHOLDERS = frozenset({
    "", "to be filled by o.e.m.", "system product name", "system version", "system manufacturer",
    "default string", "not applicable", "none", "not specified", "o.e.m.", "oem", "n/a", "unknown",
    "type1familychassis", "system serial number", "base board product name", "x.x", "0",
})

# SMBIOS 3.x Type 3 chassis types, folded to the form factors that change behaviour.
_CHASSIS = {
    **dict.fromkeys((3, 4, 5, 6, 7, 13, 15, 16, 35, 36), "desktop"),
    **dict.fromkeys((8, 9, 10, 14, 31, 32), "laptop"),
    30: "tablet", 11: "handheld", **dict.fromkeys((17, 23, 28), "server"),
}

# The many spellings OEMs use for themselves ("ASUSTeK COMPUTER INC.", "Micro-Star International").
_VENDOR_ALIASES = (
    (r"^asus", "ASUS"), (r"^dell", "Dell"), (r"^(hp|hewlett)", "HP"), (r"^lenovo", "Lenovo"),
    (r"^(micro-star|msi)", "MSI"), (r"^acer", "Acer"), (r"^microsoft", "Microsoft"),
    (r"^samsung", "Samsung"), (r"^gigabyte", "Gigabyte"), (r"^razer", "Razer"),
    (r"^apple", "Apple"), (r"^nvidia", "NVIDIA"), (r"^framework", "Framework"),
)

# ASUS appends the board code and a build number: "ProArt P14 H7407BA_000293225".
_BUILD_SUFFIX = re.compile(r"_[0-9]{6,}$")


class SystemIdentity(NamedTuple):
    """SMBIOS system fields with firmware placeholders blanked."""

    manufacturer: str = ""
    product: str = ""
    family: str = ""
    version: str = ""
    sku: str = ""
    board_vendor: str = ""
    board_product: str = ""
    chassis_code: int = 0


def clean(value: object) -> str:
    """The field's text, or ``""`` for a missing value or a firmware placeholder."""
    text = str(value or "").replace("\0", "").strip()
    return "" if text.lower() in _PLACEHOLDERS else text


def normalize_vendor(raw: str) -> str:
    """The canonical vendor name for an OEM's self-description; unknown vendors pass through."""
    return next((name for pattern, name in _VENDOR_ALIASES if re.search(pattern, raw, re.IGNORECASE)), raw)


def form_factor(chassis_code: int) -> str:
    """``laptop``/``desktop``/``tablet``/``handheld``/``server``, or ``unknown``."""
    return _CHASSIS.get(chassis_code, "unknown")


def parse_chassis(raw: object) -> int:
    try:
        return int(str(raw or "0").strip())
    except ValueError:
        return 0


def identity_from_fields(fields: Mapping[str, object]) -> SystemIdentity:
    """Build an identity from raw reader output, blanking placeholders and parsing the chassis."""
    return SystemIdentity(
        **{name: clean(fields.get(name)) for name in SystemIdentity._fields if name != "chassis_code"},
        chassis_code=parse_chassis(fields.get("chassis_code")))


def vendor(identity: SystemIdentity) -> str:
    """Canonical chassis vendor; some OEMs brand only the baseboard and leave Type 1 blank."""
    raw = identity.manufacturer or identity.board_vendor
    return normalize_vendor(raw) if raw else ""


def marketing_name(identity: SystemIdentity) -> str:
    """Best human label: vendor, then family, then the product name without a build suffix."""
    brand = vendor(identity)
    product = _BUILD_SUFFIX.sub("", identity.product)
    best = product or identity.family
    if identity.family and identity.family.lower() not in best.lower():
        best = f"{identity.family} {best}"
    if brand and best.lower().startswith(brand.lower()):
        return best
    return f"{brand} {best}".strip()


_BIOS_KEY = r"HARDWARE\DESCRIPTION\System\BIOS"
_WINDOWS_VALUES = {
    "manufacturer": "SystemManufacturer", "product": "SystemProductName", "family": "SystemFamily",
    "version": "SystemVersion", "sku": "SystemSKU", "board_vendor": "BaseBoardManufacturer",
    "board_product": "BaseBoardProduct", "chassis_code": "EnclosureType",
}
_DMI_ROOT = "/sys/class/dmi/id"
_LINUX_FILES = {
    "manufacturer": "sys_vendor", "product": "product_name", "family": "product_family",
    "version": "product_version", "sku": "product_sku", "board_vendor": "board_vendor",
    "board_product": "board_name", "chassis_code": "chassis_type",
}


def darwin_fields(hw_model: str) -> dict[str, object]:
    """Fields for a Mac from its ``hw.model`` identifier (``MacBookAir10,1``, ``Mac15,3``).

    Only ``MacBook*`` identifiers say laptop; the ``MacNN,N`` scheme carries no form factor.
    """
    family = re.sub(r"[0-9,]+$", "", hw_model)
    laptop = family.startswith("MacBook")
    return {"manufacturer": "Apple Inc.", "product": hw_model, "family": family,
            "chassis_code": 10 if laptop else 0}


def _windows_fields() -> dict[str, object]:
    import winreg

    fields: dict[str, object] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BIOS_KEY) as key:
            for name, value_name in _WINDOWS_VALUES.items():
                try:
                    fields[name] = winreg.QueryValueEx(key, value_name)[0]
                except OSError:
                    continue
    except OSError:
        pass
    return fields


def _linux_fields() -> dict[str, object]:
    return {name: facts._read_text(f"{_DMI_ROOT}/{file}", 256) for name, file in _LINUX_FILES.items()}


@functools.cache
def system_identity() -> SystemIdentity:
    """This machine's SMBIOS identity; every field is blank where the OS exposes none."""
    try:
        if sys.platform == "win32":
            return identity_from_fields(_windows_fields())
        if sys.platform == "darwin":
            return identity_from_fields(darwin_fields(facts._sysctl_str(b"hw.model")))
        if sys.platform.startswith("linux"):
            return identity_from_fields(_linux_fields())
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return SystemIdentity()
