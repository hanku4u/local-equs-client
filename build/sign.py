"""Authenticode signing of the .exe and installer (C6.3).

Signs ``dist\\LocalEQUS\\LocalEQUS.exe`` and the most recent
``dist\\LocalEQUS-Setup-X.Y.Z.exe`` with ``signtool``, timestamps via
DigiCert (or ``$SIGNING_TIMESTAMP_URL`` override), and runs
``signtool verify /pa /v`` to confirm a valid Authenticode signature.

Certificate input is one of two env-var groups:

- **.pfx file** — ``$SIGNING_CERT`` (full path) + ``$SIGNING_PASSWORD``.
- **Windows certificate store** — ``$SIGNING_THUMBPRINT`` (SHA-1
  thumbprint of the cert, hex, no separators). Use this for EV certs
  that live on a hardware token.

If both are set, the .pfx path wins. The cert and password are never
committed; the build pipeline reads them from the environment.

Run from the repo root via ``build\\sign.cmd``. Pass an explicit file
path to sign just that file; with no args the default targets are both
the bundled .exe and the latest installer setup.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DIST = _REPO_ROOT / "dist"
_BUNDLED_EXE = _DIST / "LocalEQUS" / "LocalEQUS.exe"

_DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"


def find_signtool() -> Path:
    """Resolve ``signtool.exe`` from $SIGNTOOL, PATH, or Windows SDK paths.

    Raises ``FileNotFoundError`` if no usable signtool is found.
    """
    override = os.environ.get("SIGNTOOL")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate

    on_path = shutil.which("signtool")
    if on_path:
        return Path(on_path)

    # Walk the Windows 10/11 SDK install root, find the newest signtool.exe
    # under any version + x64 / arm64 / x86 subfolder.
    candidates: list[Path] = []
    for sdk_root in (
        Path(r"C:\Program Files (x86)\Windows Kits\10\bin"),
        Path(r"C:\Program Files\Windows Kits\10\bin"),
    ):
        if not sdk_root.is_dir():
            continue
        for version_dir in sdk_root.iterdir():
            if not version_dir.is_dir():
                continue
            for arch in ("x64", "arm64", "x86"):
                exe = version_dir / arch / "signtool.exe"
                if exe.is_file():
                    candidates.append(exe)
    if candidates:
        # Sort by version-folder name; later SDK versions sort higher.
        candidates.sort(key=lambda p: p.parent.parent.name, reverse=True)
        return candidates[0]

    raise FileNotFoundError(
        "Could not find signtool.exe. Install a recent Windows SDK, or set "
        "$SIGNTOOL to its full path."
    )


def cert_args() -> list[str]:
    """Return the ``/f /p`` or ``/sha1`` flags for the configured cert.

    Raises ``RuntimeError`` if neither env-var group is set.
    """
    pfx_path = os.environ.get("SIGNING_CERT")
    pfx_password = os.environ.get("SIGNING_PASSWORD")
    thumbprint = os.environ.get("SIGNING_THUMBPRINT")

    if pfx_path:
        if not pfx_password:
            raise RuntimeError(
                "$SIGNING_CERT is set but $SIGNING_PASSWORD is missing."
            )
        return ["/f", pfx_path, "/p", pfx_password]

    if thumbprint:
        # /sha1 selects the certificate by thumbprint; signtool defaults to
        # the user's MY store unless /sm is given.
        return ["/sha1", thumbprint]

    raise RuntimeError(
        "No signing cert configured. Set either $SIGNING_CERT + "
        "$SIGNING_PASSWORD (for a .pfx file) or $SIGNING_THUMBPRINT (for a "
        "cert in the Windows certificate store)."
    )


def sign_args(
    signtool: Path, target: Path, *, timestamp_url: str, cert: list[str]
) -> list[str]:
    return [
        str(signtool),
        "sign",
        "/v",
        "/fd",
        "SHA256",
        "/tr",
        timestamp_url,
        "/td",
        "SHA256",
        *cert,
        str(target),
    ]


def verify_args(signtool: Path, target: Path) -> list[str]:
    """Authenticode policy verification (``/pa``)."""
    return [str(signtool), "verify", "/pa", "/v", str(target)]


def latest_installer() -> Path | None:
    """Newest ``LocalEQUS-Setup-*.exe`` under ``dist/``."""
    matches = sorted(
        _DIST.glob("LocalEQUS-Setup-*.exe"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def default_targets() -> list[Path]:
    """Return the files we sign by default if no explicit target is passed."""
    targets: list[Path] = []
    if _BUNDLED_EXE.is_file():
        targets.append(_BUNDLED_EXE)
    installer = latest_installer()
    if installer is not None:
        targets.append(installer)
    return targets


def sign_one(signtool: Path, target: Path, timestamp_url: str) -> int:
    """Sign + verify a single file. Returns the first non-zero exit code."""
    cert = cert_args()
    print(f"Signing {target}", flush=True)
    rc = subprocess.call(
        sign_args(signtool, target, timestamp_url=timestamp_url, cert=cert),
        cwd=_REPO_ROOT,
    )
    if rc != 0:
        return rc
    print(f"Verifying {target}", flush=True)
    return subprocess.call(verify_args(signtool, target), cwd=_REPO_ROOT)


def run(targets: list[Path] | None = None) -> int:
    signtool = find_signtool()
    timestamp_url = os.environ.get("SIGNING_TIMESTAMP_URL", _DEFAULT_TIMESTAMP_URL)

    to_sign = targets if targets else default_targets()
    if not to_sign:
        raise FileNotFoundError(
            f"No targets to sign. Run build\\nuitka.cmd and build\\installer.cmd "
            f"first, or pass an explicit file path. Expected {_BUNDLED_EXE} or "
            f"dist\\LocalEQUS-Setup-*.exe."
        )

    for target in to_sign:
        if not target.is_file():
            raise FileNotFoundError(f"Cannot sign missing file: {target}")
        rc = sign_one(signtool, target, timestamp_url)
        if rc != 0:
            return rc
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help=(
            "File to sign. Omit to sign both dist\\LocalEQUS\\LocalEQUS.exe "
            "and the latest dist\\LocalEQUS-Setup-*.exe."
        ),
    )
    args = parser.parse_args()
    try:
        return run(targets=[args.target] if args.target else None)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
