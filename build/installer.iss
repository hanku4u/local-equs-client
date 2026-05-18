; Inno Setup script for Local EQUS Client (C6.2).
;
; Compile from the repo root via ``build\installer.cmd`` — that wrapper
; reads the version from ``pyproject.toml`` and passes it through as
; ``/DAppVersion=X.Y.Z``. Do not edit the version here.
;
; Prerequisite: ``build\nuitka.cmd`` has produced ``dist\LocalEQUS\``.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Local EQUS Client"
#define AppPublisher "LocalEQUS"
#define AppExeName "LocalEQUS.exe"

[Setup]
; A stable GUID identifies the app across versions for upgrades / uninstall.
; Generated once for this product; do not change.
AppId={{6B2F4D8A-9C1E-4F3B-A2D7-5E8C9B1F0D6A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}

; Per-user install — no UAC, no admin required. Matches the
; %LOCALAPPDATA%\Programs\LocalEQUS spec.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\LocalEQUS
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes

; Output the setup .exe to dist/ alongside the Nuitka folder so all
; build artifacts live in one place.
OutputDir=..\dist
OutputBaseFilename=LocalEQUS-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes

UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

; Standard /SILENT and /VERYSILENT support is built into Inno Setup; no
; extra config needed.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Pull in everything Nuitka produced.
Source: "..\dist\LocalEQUS\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{userprograms}\{#AppName}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
; Optional 'Launch LocalEQUS' checkbox on the final wizard page; suppressed
; in /SILENT and /VERYSILENT installs by Inno Setup's standard flags.
Filename: "{app}\{#AppExeName}"; \
    Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

; User state under %LOCALAPPDATA%\LocalEQUS (config.toml, state.db,
; telemetry queue) is intentionally NOT touched on uninstall. The
; default uninstaller only removes what we installed under {app}.
