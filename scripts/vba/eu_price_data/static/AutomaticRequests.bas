Sub AutomaticRequest()
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Sheets("REQUEST_TABLE")

    Dim lastRow As Long
    lastRow = ws.Cells(ws.Rows.count, 2).End(xlUp).row ' Check column B

    Dim i As Long

    For i = 7 To lastRow
        If ws.Cells(i, 2).value = "NO" Then
            ' Set flag to trigger request
            ws.Cells(i, 2).value = "YES"

            ' Run Datastream request
            Application.Run "'" & ThisWorkbook.name & "'!ProcessRequestTable"

            ' Wait until column L is populated
            Dim waitStart As Double: waitStart = Timer
            Do
                DoEvents
                Application.Wait Now + TimeValue("0:00:10")
                If ws.Cells(i, 12).value <> "" Then Exit Do

                If Timer - waitStart > 7200 Then ' 2-hour timeout
                    MsgBox "Timeout: request at row " & i & " did not complete in 2 hours.", vbExclamation
                    Exit Sub
                End If
            Loop

            ' Reset flag
            ws.Cells(i, 2).value = "NO"
        End If
    Next i

    MsgBox "All requests completed.", vbInformation
End Sub

