; Inno Setup script for Negscan. Driven by packaging/build.py, which passes the version,
; the PyInstaller output folder and where to write the installer.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef MySourceDir
  #define MySourceDir "..\dist\Negscan"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\dist\release"
#endif

[Setup]
AppId={{7F3B2A64-9C41-4E0D-9E2E-4D1A6C8B5A21}
AppName=Negscan
AppVersion={#MyAppVersion}
AppPublisher=Negscan
DefaultDirName={autopf}\Negscan
DefaultGroupName=Negscan
DisableProgramGroupPage=yes
OutputDir={#MyOutputDir}
OutputBaseFilename=Negscan-{#MyAppVersion}-windows-x86_64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; Per-user installs need no elevation; a machine-wide install asks for it.
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\Negscan.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Negscan"; Filename: "{app}\Negscan.exe"
Name: "{autodesktop}\Negscan"; Filename: "{app}\Negscan.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Negscan.exe"; Description: "Start Negscan"; Flags: nowait postinstall skipifsilent
