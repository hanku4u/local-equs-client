"""Unit tests for the signing driver (C6.3).

We don't actually sign anything — signtool needs a real cert and the
Windows SDK. Smoke-test the pure helpers: signtool discovery, cert-arg
selection from env vars, and the assembled command lines.
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
def sign_module():
    return _load("sign", _BUILD_DIR / "sign.py")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Clear every env var the driver reads so each test starts neutral."""
    for var in (
        "SIGNTOOL",
        "SIGNING_CERT",
        "SIGNING_PASSWORD",
        "SIGNING_THUMBPRINT",
        "SIGNING_TIMESTAMP_URL",
    ):
        monkeypatch.delenv(var, raising=False)


# ----- find_signtool ----------------------------------------------------


def test_find_signtool_uses_env_var_when_set(
    sign_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    fake = tmp_path / "signtool.exe"
    fake.write_text("")
    monkeypatch.setenv("SIGNTOOL", str(fake))
    assert sign_module.find_signtool() == fake


# The negative path for find_signtool (no SDK installed, no PATH entry, no
# $SIGNTOOL) depends on the host machine's SDK install and is awkward to
# mock without rewriting the function. It's exercised by integration when
# someone runs sign.cmd without an SDK.


# ----- cert_args --------------------------------------------------------


def test_cert_args_uses_pfx_when_both_set(
    sign_module, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("SIGNING_CERT", r"C:\secure\codesign.pfx")
    monkeypatch.setenv("SIGNING_PASSWORD", "hunter2")
    args = sign_module.cert_args()
    assert args == ["/f", r"C:\secure\codesign.pfx", "/p", "hunter2"]


def test_cert_args_falls_back_to_thumbprint(
    sign_module, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("SIGNING_THUMBPRINT", "a" * 40)
    args = sign_module.cert_args()
    assert args == ["/sha1", "a" * 40]


def test_cert_args_pfx_without_password_raises(
    sign_module, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("SIGNING_CERT", r"C:\secure\codesign.pfx")
    with pytest.raises(RuntimeError, match="SIGNING_PASSWORD"):
        sign_module.cert_args()


def test_cert_args_no_env_set_raises(
    sign_module, clean_env: None
) -> None:
    with pytest.raises(RuntimeError, match="No signing cert configured"):
        sign_module.cert_args()


def test_cert_args_prefers_pfx_over_thumbprint(
    sign_module, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.setenv("SIGNING_CERT", r"C:\secure\codesign.pfx")
    monkeypatch.setenv("SIGNING_PASSWORD", "hunter2")
    monkeypatch.setenv("SIGNING_THUMBPRINT", "a" * 40)
    args = sign_module.cert_args()
    assert "/f" in args
    assert "/sha1" not in args


# ----- sign_args / verify_args -----------------------------------------


def test_sign_args_includes_timestamp_and_sha256(sign_module) -> None:
    signtool = Path("C:/sign.exe")
    target = Path("dist/X.exe")
    args = sign_module.sign_args(
        signtool,
        target,
        timestamp_url="http://timestamp.example.com",
        cert=["/f", "cert.pfx", "/p", "pw"],
    )
    assert args[0] == str(signtool)
    assert "sign" in args
    assert "/fd" in args and "SHA256" in args
    assert "/tr" in args
    assert "http://timestamp.example.com" in args
    assert "/td" in args
    assert args[-1] == str(target)


def test_verify_args_uses_pa_policy(sign_module) -> None:
    signtool = Path("C:/sign.exe")
    target = Path("dist/X.exe")
    args = sign_module.verify_args(signtool, target)
    assert args == [str(signtool), "verify", "/pa", "/v", str(target)]


# ----- target discovery ------------------------------------------------


def test_default_targets_picks_exe_and_latest_installer(
    sign_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist = tmp_path / "dist"
    bundled = dist / "LocalEQUS" / "LocalEQUS.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("")
    old_setup = dist / "LocalEQUS-Setup-0.0.1.exe"
    old_setup.write_text("")
    new_setup = dist / "LocalEQUS-Setup-0.0.2.exe"
    new_setup.write_text("")
    import os
    import time

    now = time.time()
    os.utime(old_setup, (now - 100, now - 100))
    os.utime(new_setup, (now, now))

    monkeypatch.setattr(sign_module, "_DIST", dist)
    monkeypatch.setattr(sign_module, "_BUNDLED_EXE", bundled)

    targets = sign_module.default_targets()
    assert bundled in targets
    assert new_setup in targets
    assert old_setup not in targets


def test_default_targets_empty_when_dist_missing(
    sign_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sign_module, "_DIST", tmp_path / "nope")
    monkeypatch.setattr(sign_module, "_BUNDLED_EXE", tmp_path / "nope" / "no.exe")
    assert sign_module.default_targets() == []
