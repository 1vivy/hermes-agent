from __future__ import annotations

import subprocess

import pytest

from hermes_platform.host import facts, products, smbios

# Measured on an ASUS ProArt P14 (Windows registry SMBIOS copy).
_PROART_P14 = {
    "manufacturer": "ASUSTeK COMPUTER INC.", "product": "ProArt P14 H7407BA_000293225",
    "family": "ProArt P14", "version": "1.0", "sku": "", "board_vendor": "ASUSTeK COMPUTER INC.",
    "board_product": "H7407BA", "chassis_code": "10",
}


def test_placeholders_are_blanked_and_chassis_parsed() -> None:
    identity = smbios.identity_from_fields({
        "manufacturer": "To Be Filled By O.E.M.", "product": "System Product Name\0",
        "board_vendor": "Micro-Star International Co., Ltd.", "chassis_code": "junk",
    })
    assert (identity.manufacturer, identity.product, identity.chassis_code) == ("", "", 0)
    assert smbios.vendor(identity) == "MSI"  # Type 2 brand when Type 1 is blank


def test_proart_identity() -> None:
    identity = smbios.identity_from_fields(_PROART_P14)
    assert smbios.vendor(identity) == "ASUS"
    assert smbios.marketing_name(identity) == "ASUS ProArt P14 H7407BA"
    assert smbios.form_factor(identity.chassis_code) == "laptop"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (_PROART_P14, "asus-proart-p14"),
        ({"manufacturer": "ASUSTeK COMPUTER INC.", "product": "ASUS ProArt P16 H7607BA"}, "asus-proart-p16"),
        ({"manufacturer": "ASUSTeK COMPUTER INC.", "product": "GR1X"}, "asus-proart-gr1x"),
        # A code only on the baseboard still names the machine.
        ({"manufacturer": "ASUSTeK COMPUTER INC.", "product": "", "board_product": "H7407BA"}, "asus-proart-p14"),
        # The model code must be a whole token, and the vendor must match.
        ({"manufacturer": "ASUSTeK COMPUTER INC.", "product": "ProArt H7407BAX"}, None),
        ({"manufacturer": "Dell Inc.", "product": "GR1X"}, None),
        ({}, None),
    ],
)
def test_recognize_machine(fields, expected) -> None:
    machine = products.recognize_machine(smbios.identity_from_fields(fields))
    assert (machine.id if machine else None) == expected


@pytest.mark.parametrize(
    ("hw_model", "family", "form_factor"),
    [("MacBookAir10,1", "MacBookAir", "laptop"), ("MacBookPro18,3", "MacBookPro", "laptop"),
     ("Mac14,3", "Mac", "unknown"), ("Macmini9,1", "Macmini", "unknown")],
)
def test_darwin_fields_from_hw_model(hw_model, family, form_factor) -> None:
    identity = smbios.identity_from_fields(smbios.darwin_fields(hw_model))
    assert (identity.family, smbios.vendor(identity)) == (family, "Apple")
    assert smbios.form_factor(identity.chassis_code) == form_factor


def _no_process(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("smbios spawned a process"))


@pytest.mark.platforms("linux")
def test_linux_identity_reads_sysfs_without_a_process(monkeypatch, tmp_path) -> None:
    for name, value in {"sys_vendor": "ASUSTeK COMPUTER INC.\n", "product_name": "GR1X\n",
                        "chassis_type": "3\n"}.items():
        (tmp_path / name).write_text(value, encoding="utf-8")
    monkeypatch.setattr(smbios, "_DMI_ROOT", str(tmp_path))
    _no_process(monkeypatch)
    smbios.system_identity.cache_clear()
    try:
        identity = smbios.system_identity()
        assert (identity.product, identity.chassis_code, identity.family) == ("GR1X", 3, "")
        assert products.known_machine().id == "asus-proart-gr1x"
    finally:
        smbios.system_identity.cache_clear()


@pytest.mark.platforms("macos")
def test_macos_identity_reads_sysctl_without_a_process(monkeypatch) -> None:
    _no_process(monkeypatch)
    smbios.system_identity.cache_clear()
    try:
        identity = smbios.system_identity()
        assert identity.product == facts._sysctl_str(b"hw.model") != ""
        assert smbios.vendor(identity) == "Apple"
        assert products.known_machine() is None
    finally:
        smbios.system_identity.cache_clear()


@pytest.mark.platforms("windows")
def test_windows_identity_reads_the_registry_without_a_process(monkeypatch) -> None:
    _no_process(monkeypatch)
    smbios.system_identity.cache_clear()
    try:
        assert isinstance(smbios.system_identity(), smbios.SystemIdentity)
    finally:
        smbios.system_identity.cache_clear()
