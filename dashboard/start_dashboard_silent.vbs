Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c .\dashboard\run_dashboard.bat", 0, False
