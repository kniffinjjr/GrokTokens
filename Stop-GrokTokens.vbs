' GrokTokens - silent stop
Option Explicit

Dim sh, fso, root, pidFile, pid, wmi, procs, p, port, url
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pidFile = root & "\GrokTokens.pid"
port = "8765"
If Len(sh.Environment("PROCESS").Item("GROKTOKENS_PORT")) > 0 Then
  port = sh.Environment("PROCESS").Item("GROKTOKENS_PORT")
End If
url = "http://127.0.0.1:" & port & "/api/health"

On Error Resume Next
If fso.FileExists(pidFile) Then
  pid = Trim(fso.OpenTextFile(pidFile, 1).ReadAll)
  If IsNumeric(pid) Then
    sh.Run "taskkill /F /PID " & pid, 0, True
  End If
  fso.DeleteFile pidFile, True
End If

Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery( _
  "Select * from Win32_Process Where Name='pythonw.exe' OR Name='python.exe'")
For Each p In procs
  If InStr(1, p.CommandLine & "", "server.py", 1) > 0 Then
    If InStr(1, p.CommandLine & "", "GrokTokens", 1) > 0 _
       Or InStr(1, p.CommandLine & "", root, 1) > 0 Then
      p.Terminate
    End If
  End If
Next
On Error GoTo 0
