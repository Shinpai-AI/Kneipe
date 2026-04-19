[Setup]
AppName=Kneipen-Schlägerei
AppVersion=1.5.1
AppPublisher=Shinpai-AI
DefaultDirName={localappdata}\Kneipe
DefaultGroupName=Kneipen-Schlägerei
OutputBaseFilename=Kneipe-Setup
Compression=lzma
SolidCompression=yes
SetupIconFile=installer-build\kneipe.ico
WizardStyle=modern
UninstallDisplayIcon={app}\kneipe.ico

[Files]
Source: "installer-build\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\Kneipen-Schlägerei"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; IconFilename: "{app}\kneipe.ico"; WorkingDir: "{app}"
Name: "{commondesktop}\Kneipen-Schlägerei"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; IconFilename: "{app}\kneipe.ico"; WorkingDir: "{app}"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\kneipe-tray.py"""; Description: "Kneipen-Schlägerei starten"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"
