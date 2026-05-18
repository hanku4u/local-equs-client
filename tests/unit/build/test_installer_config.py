"""Unit tests for the Inno Setup driver (C6.2).

We don't actually compile the installer — that needs ``iscc.exe``
installed and a built Nuitka folder on disk. Instead, smoke-test the
pure helpers: iscc.exe discovery, output-folder verification, and
command-line assembly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUILD_DIR = _REPO_ROOT / "build"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def installer_module():
    # Side-load build_config first; build_installer imports it by bare name.
    _load("build_config", _BUILD_DIR / "build_config.py")
    return _load("build_installer", _BUILD_DIR / "build_installer.py")


def test_iss_file_exists() -> None:
    assert (_BUILD_DIR / "installer.iss").is_file()


def test_iss_declares_per_user_install() -> None:
    text = (_BUILD_DIR / "installer.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in text
    assert "{localappdata}\\Programs\\LocalEQUS" in text


def test_iss_uses_appversion_variable() -> None:
    text = (_BUILD_DIR / "installer.iss").read_text(encoding="utf-8")
    assert "{#AppVersion}" in text
    assert "LocalEQUS-Setup-{#AppVersion}" in text


def test_iss_bundles_nuitka_output_folder() -> None:
    text = (_BUILD_DIR / "installer.iss").read_text(encoding="utf-8")
    assert "..\\dist\\LocalEQUS\\*" in text


def test_iss_includes_launch_post_install_step() -> None:
    text = (_BUILD_DIR / "installer.iss").read_text(encoding="utf-8")
    # "skipifsilent" is what makes /SILENT and /VERYSILENT skip the launch.
    assert "skipifsilent" in text


def test_iscc_args_passes_version_define(installer_module) -> None:
    fake_iscc = Path("C:/fake/iscc.exe")
    args = installer_module.iscc_args(fake_iscc, "1.2.3")
    assert args[0] == str(fake_iscc)
    assert "/DAppVersion=1.2.3" in args
    assert args[-1].endswith("installer.iss")


def test_find_iscc_uses_env_var_when_set(
    installer_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "iscc.exe"
    fake.write_text("")
    monkeypatch.setenv("ISCC", str(fake))
    assert installer_module.find_iscc() == fake


def test_find_iscc_raises_when_unavailable(
    installer_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ISCC", raising=False)
    monkeypatch.setattr(installer_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(installer_module, "_DEFAULT_ISCC_PATHS", ())
    with pytest.raises(FileNotFoundError):
        installer_module.find_iscc()


def test_verify_nuitka_output_raises_when_missing(
    installer_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(installer_module, "_NUITKA_OUTPUT", Path("/no/such.exe"))
    with pytest.raises(FileNotFoundError):
        installer_module.verify_nuitka_output()
