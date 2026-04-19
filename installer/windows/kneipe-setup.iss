[Setup]
AppName=Kneipen-Schlägerei
AppVersion=1.5.0
AppPublisher=Shinpai-AI
DefaultDirName={autopf}\Kneipe
DefaultGroupName=Kneipen-Schlägerei
OutputBaseFilename=Kneipe-Setup-v1.5.0
Compression=lzma
SolidCompression=yes
SetupIconFile=installer-build\favicon.ico
WizardStyle=modern

[Files]
Source: "installer-build\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\Kneipen-Schlägerei"; Filename: "{app}\kneipe-tray.pyw"; IconFilename: "{app}\favicon.ico"
Name: "{commondesktop}\Kneipen-Schlägerei"; Filename: "{app}\kneipe-tray.pyw"; IconFilename: "{app}\favicon.ico"

[Run]
Filename: "{app}\kneipe-tray.pyw"; Description: "Kneipen-Schlägerei starten"; Flags: nowait postinstall skipifsilent
