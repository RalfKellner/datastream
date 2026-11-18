Sub GenerateDataRows()
    Dim i As Long, targetRow As Long, colOffset As Long
    Dim var As Variant, varList As Variant
    Dim sheetName As String

    ' Starting row in the request table (adjust if needed)
    targetRow = 7

    ' Set column offset: 1 means data starts in column B (A is skipped)
    colOffset = 1
    sheetName = "Sheet"

    ' List of datastream time-series variable codes
    varList = Array("RI", "PO", "PH", "PL", "P", "VO", "MV", "MTBV", "AF", "UP")

    Const eurList As String = "|RI|MV|UP|"

    Application.ScreenUpdating = False

    ' Loop for each block (EU001 ... EU036)
    For i = 1 To 36
        For Each var In varList

            ' Default row (always)
            WriteRow ActiveSheet, targetRow, colOffset, i, _
                     "DPL#(X(" & var & "),6)", CStr(var), sheetName
            targetRow = targetRow + 1

            ' Additional EUR row for variables in eurList
            If InStr(1, eurList, "|" & var & "|", vbBinaryCompare) > 0 Then
                WriteRow ActiveSheet, targetRow, colOffset, i, _
                         "DPL#(X(" & var & ")~E,6)", var & "_EU", sheetName
                targetRow = targetRow + 1
            End If

        Next var
    Next i

    Application.ScreenUpdating = True
End Sub

Private Sub WriteRow(ws As Worksheet, r As Long, colOffset As Long, i As Long, _
                     spec As String, fileVar As String, sheetName As String)
    With ws
        .Cells(r, 1 + colOffset).value = "NO"
        .Cells(r, 2 + colOffset).value = "TSL"
        .Cells(r, 3 + colOffset).value = "RCM$"
        .Cells(r, 4 + colOffset).value = "L#EU" & Format(i, "000")
        .Cells(r, 5 + colOffset).value = spec
        .Cells(r, 6 + colOffset).value = "1992-12-31"
        .Cells(r, 7 + colOffset).ClearContents
        .Cells(r, 8 + colOffset).value = "Daily"
        .Cells(r, 9 + colOffset).ClearContents
        .Cells(r, 10 + colOffset).formula = "='D:\Datastream\PriceData\EU\" & _
            Format(i, "00") & "\[" & fileVar & "_" & Format(i, "00") & ".xlsx]" & sheetName & "'!R1C1"
    End With
End Sub

