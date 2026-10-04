Attribute VB_Name = "AutomaticRequest_EU"
Option Explicit

' Runs the Datastream request table row by row for the EU Worldscope rows created by
' GenerateFirmDataRows_EU. A row is processed if its spec in column F is DPL#(X(<VAR>),6) with <VAR>
' in VAR_FILTER (empty filter = all 15 baseline variables).

Sub AutomaticRequest_EU()

    '------------------------------------------------------------
    '  SET-UP
    '------------------------------------------------------------
    Const FIRST_ROW As Long = 7
    Const COL_FLAG As Long = 2          ' column B: NO / YES
    Const COL_SPEC As Long = 6          ' column F: DPL#(X(WC01001),6)
    Const COL_STATUS As Long = 12       ' column L: filled by Datastream after the request
    Const TIMEOUT_MIN As Long = 120     ' per request
    Const SKIP_DONE As Boolean = True   ' skip rows whose column L is already filled (resume after a break)

    ' Variables to run; leave empty ("") to run all variables below
    Const VAR_FILTER As String = ""     ' e.g. "|WC03501|WC01751|" to rerun selected variables

    Const ALL_VARS As String = "|WC01001|WC01250|WC01251|WC01751|WC18198|WC02003|WC02999|" & _
                               "WC03051|WC03251|WC03255|WC03501|WC04601|WC08231|WC08301|WC08326|"

    Dim ws As Worksheet
    Set ws = ThisWorkbook.Sheets("REQUEST_TABLE")

    Dim lastRow As Long, i As Long, nDone As Long
    Dim spec As String, var As String, oldStatus As String
    Dim deadline As Date

    lastRow = ws.Cells(ws.Rows.Count, COL_FLAG).End(xlUp).Row

    For i = FIRST_ROW To lastRow

        spec = CStr(ws.Cells(i, COL_SPEC).Value)
        var = VarFromSpec(spec)              ' "" if the spec is not of the form DPL#(X(<VAR>),6)

        If ws.Cells(i, COL_FLAG).Value = "NO" And var <> "" _
           And InStr(1, ALL_VARS, "|" & var & "|", vbBinaryCompare) > 0 _
           And (VAR_FILTER = "" Or InStr(1, VAR_FILTER, "|" & var & "|", vbBinaryCompare) > 0) Then

            oldStatus = CStr(ws.Cells(i, COL_STATUS).Value)

            If Not (SKIP_DONE And oldStatus <> "") Then

                ' Set flag and run the Datastream request
                ws.Cells(i, COL_FLAG).Value = "YES"
                Application.Run "'" & ThisWorkbook.Name & "'!ProcessRequestTable"

                ' Wait until column L changes
                deadline = Now + TimeSerial(0, TIMEOUT_MIN, 0)
                Do
                    DoEvents
                    Application.Wait Now + TimeValue("0:00:10")
                    If CStr(ws.Cells(i, COL_STATUS).Value) <> oldStatus Then Exit Do
                    If Now > deadline Then
                        ws.Cells(i, COL_FLAG).Value = "NO"
                        MsgBox "Timeout: request at row " & i & " (" & var & ", " & _
                               ws.Cells(i, 5).Value & ") did not complete in " & TIMEOUT_MIN & " minutes.", _
                               vbExclamation
                        Exit Sub
                    End If
                Loop

                ' Reset flag
                ws.Cells(i, COL_FLAG).Value = "NO"
                nDone = nDone + 1
            End If
        End If
    Next i

    MsgBox nDone & " requests completed.", vbInformation

End Sub

' Returns the variable code from "DPL#(X(WC01001),6)", or "" if the spec has a different form.
Private Function VarFromSpec(ByVal spec As String) As String
    Dim p1 As Long, p2 As Long
    VarFromSpec = ""
    If Left$(spec, 7) <> "DPL#(X(" Then Exit Function
    p1 = 8
    p2 = InStr(p1, spec, ")")
    If p2 = 0 Then Exit Function
    If Mid$(spec, p2, 4) <> "),6)" Then Exit Function
    VarFromSpec = Mid$(spec, p1, p2 - p1)
End Function
