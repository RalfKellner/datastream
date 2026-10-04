Attribute VB_Name = "AutomaticRequest_EU"
Option Explicit

' Runs the Datastream request table row by row: every row with "NO" in column B and a request in column F.

Sub AutomaticRequest()

    Const FIRST_ROW As Long = 7
    Const COL_FLAG As Long = 2          ' column B: NO / YES
    Const COL_SPEC As Long = 6          ' column F: request
    Const COL_STATUS As Long = 12       ' column L: filled by Datastream after the request
    Const TIMEOUT_MIN As Long = 120     ' per request
    Const SKIP_DONE As Boolean = True   ' skip rows whose column L is already filled (resume after a break)

    Dim ws As Worksheet
    Set ws = ThisWorkbook.Sheets("REQUEST_TABLE")

    Dim lastRow As Long, i As Long, nDone As Long
    Dim oldStatus As String
    Dim deadline As Date

    lastRow = ws.Cells(ws.Rows.Count, COL_FLAG).End(xlUp).Row

    For i = FIRST_ROW To lastRow

        If ws.Cells(i, COL_FLAG).Value = "NO" And ws.Cells(i, COL_SPEC).Value <> "" Then

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
                        MsgBox "Timeout: request at row " & i & " did not complete in " & _
                               TIMEOUT_MIN & " minutes.", vbExclamation
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
