' Launches main.py silently and restarts if it crashes.
' Clean exit (code 0) via Ctrl+Alt+Q does NOT restart.
' Runs non-elevated so audio endpoints stay accessible.

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
python = """" & base & "\.venv\Scripts\pythonw.exe"""
script = """" & base & "\main.py"""
cmd = python & " " & script
Do
    exitCode = WshShell.Run(cmd, 0, True)
    If exitCode = 0 Then Exit Do
    WScript.Sleep 3000
Loop
