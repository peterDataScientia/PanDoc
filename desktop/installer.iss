#define MyAppName "PanDockLab"
#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif
#define MyAppExeName "PanDoc.exe"

[Setup]
AppId={{A91B9777-8E67-47BE-A712-7C23418C5275}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=PanDockLab
DefaultDirName={localappdata}\Programs\PanDoc
DefaultGroupName=PanDockLab
OutputDir=..\dist-installer
OutputBaseFilename=PanDoc-Setup-Windows-x64
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
SetupIconFile=vendor\pandocklab.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\PanDoc\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PanDockLab"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\PanDockLab"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[InstallDelete]
; Upgrade from the old PanDoc shortcut names, without touching project data.
Type: files; Name: "{userprograms}\\PanDoc\\PanDoc.lnk"
Type: dirifempty; Name: "{userprograms}\\PanDoc"
Type: files; Name: "{autodesktop}\\PanDoc.lnk"

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PanDockLab"; Flags: nowait postinstall skipifsilent
