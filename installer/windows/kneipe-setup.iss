[Setup]
AppName=Kneipen-Schlägerei
AppVersion=1.5.0
AppPublisher=Shinpai-AI
DefaultDirName={autopf}\Kneipe
DefaultGroupName=Kneipen-Schlägerei
OutputBaseFilename=Kneipe-Setup-v1.5.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "installer-build\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\Kneipen-Schlägerei"; Filename: "{app}\Kneipe.bat"; IconFilename: "{app}\kneipe.png"
Name: "{commondesktop}\Kneipen-Schlägerei"; Filename: "{app}\Kneipe.bat"; IconFilename: "{app}\kneipe.png"

[Run]
Filename: "{app}\Kneipe.bat"; Description: "Kneipen-Schlägerei starten"; Flags: nowait postinstall skipifsilent shellexec
