; Inno Setup script for ILS Water Corp Invoice Generator

#define AppName "ILS Water Corp Invoice Generator"
#define AppVersion "2.3.12"
#define AppPublisher "RealTV"
#define AppExeName "ILS Water Corp Invoice Generator.exe"
#define AppId "{{B4A9A1B4-6F4B-4E6E-9C0F-8A7B9E3F2C10}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={commonpf}\{#AppPublisher}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=ILS_WaterCorp_Invoice_Generator_Setup
SetupIconFile=..\assets\ILS_WC.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "..\dist\ILS Water Corp Invoice Generator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startmenuicon
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
