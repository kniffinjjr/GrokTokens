' GrokTokens - silent start (no console window)
' Starts pythonw server.py if not already healthy, then opens the dashboard.
Option Explicit

Dim sh, fso, root, url, port, py, cmd, i, ok, candidates, c
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
port = "8765"
If Len(sh.Environment("PROCESS").Item("GROKTOKENS_PORT")) > 0 Then
  port = sh.Environment("PROCESS").Item("GROKTOKENS_PORT")
End If
url = "http://127.0.0.1:" & port & "/"

Function IsHealthy()
  On Error Resume Next
  Dim h
  Set h = CreateObject("MSXML2.XMLHTTP")
  h.Open "GET", url & "api/health", False
  h.setRequestHeader "Cache-Control", "no-cache"
  h.Send
  If Err.Number <> 0 Then
    IsHealthy = False
    Err.Clear
    Exit Function
  End If
  IsHealthy = (h.Status = 200)
  On Error GoTo 0
End Function

Function FindPythonW()
  Dim pathEnv, parts, i, p, ver, base
  candidates = Array( _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"), _
    sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"), _
    "C:\Python314\pythonw.exe", _
    "C:\Python313\pythonw.exe", _
    "C:\Python312\pythonw.exe", _
    "C:\Python311\pythonw.exe" _
  )
  For Each c In candidates
    If fso.FileExists(c) Then
      FindPythonW = c
      Exit Function
    End If
  Next
  pathEnv = sh.ExpandEnvironmentStrings("%PATH%")
  parts = Split(pathEnv, ";")
  For i = 0 To UBound(parts)
    p = Trim(parts(i))
    If Len(p) > 0 Then
      If fso.FileExists(p & "\pythonw.exe") Then
        FindPythonW = p & "\pythonw.exe"
        Exit Function
      End If
    End If
  Next
  FindPythonW = ""
End Function

If IsHealthy() Then
  sh.Run url, 1, False
  WScript.Quit 0
End If

If Not fso.FileExists(root & "\server.py") Then
  MsgBox "Missing server.py in:" & vbCrLf & root, vbCritical, "GrokTokens"
  WScript.Quit 1
End If

If Not fso.FileExists(root & "\config.json") And fso.FileExists(root & "\config.example.json") Then
  fso.CopyFile root & "\config.example.json", root & "\config.json", True
End If

py = FindPythonW()
If Len(py) = 0 Then
  MsgBox "pythonw.exe not found." & vbCrLf & vbCrLf & _
         "Install Python 3 from https://www.python.org/downloads/" & vbCrLf & _
         "and ensure pythonw.exe is on PATH.", vbCritical, "GrokTokens"
  WScript.Quit 1
End If

cmd = """" & py & """ """ & root & "\server.py"""
sh.CurrentDirectory = root
sh.Run cmd, 0, False

ok = False
For i = 1 To 40
  WScript.Sleep 250
  If IsHealthy() Then
    ok = True
    Exit For
  End If
Next

If ok Then
  sh.Run url, 1, False
  WScript.Quit 0
End If

MsgBox "Could not start GrokTokens on " & url & vbCrLf & vbCrLf & _
       "Check that port " & port & " is free and see GrokTokens.log if present.", _
       vbExclamation, "GrokTokens"
WScript.Quit 1
