#define MyAppName "PanDoc"
#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif
#define MyAppExeName "PanDoc.exe"

[Setup]
AppId={{A91B9777-8E67-47BE-A712-7C23418C5275}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=PanDoc
DefaultDirName={localappdata}\Programs\PanDoc
DefaultGroupName=PanDoc
OutputDir=..\dist-installer
OutputBaseFilename=PanDoc-Setup-Windows-x64
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\PanDoc\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PanDoc"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\PanDoc"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PanDoc"; Flags: nowait postinstall skipifsilent
