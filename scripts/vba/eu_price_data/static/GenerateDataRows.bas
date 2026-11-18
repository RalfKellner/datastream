Sub GenerateDataRows()
    Dim i As Long, targetRow As Long, colOffset As Long
    Dim var As Variant, varList As Variant
    Dim sheetName As String

    ' Starting row in the request table (adjust if needed)
    targetRow = 7

    ' Set column offset: 1 means data starts in column B (A is skipped)
    colOffset = 1

    ' Loop for each block (from EU001 up to EU022)
    For i = 1 To 36
        ' --- Static row ---
        With ActiveSheet
            ' Set the first five columns
            .Cells(targetRow, 1 + colOffset).value = "NO"
            .Cells(targetRow, 2 + colOffset).value = "S"
            .Cells(targetRow, 3 + colOffset).value = "RC$"
            .Cells(targetRow, 4 + colOffset).value = "L#EU" & Format(i, "000")
            .Cells(targetRow, 5 + colOffset).value = "DSCD,BDATE,ENAME,EXMNEM,GEOGN,ISIN,ISINID,LOC,PCUR,TRAC,TYPE,WC05601,RIC,TR1,TR1N,TR2,TR2N,TR3,TR3N,WC06105,WC06035,ICBIN,ICBSSN,ICBSN,ICBSUN"
            ' Leave columns 6, 7, and 8 blank
            .Cells(targetRow, 6 + colOffset).value = ""
            .Cells(targetRow, 7 + colOffset).value = ""
            .Cells(targetRow, 8 + colOffset).value = ""
            .Cells(targetRow, 9 + colOffset).value = ""
            ' Place the external directory path (formula) in the 9th column (data column)
            .Cells(targetRow, 10 + colOffset).formula = "='D:\Datastream\PriceData\EU\" & _
                Format(i, "00") & "\[STATIC_" & Format(i, "00") & ".xlsx]Sheet'!R1C1"
        End With
        targetRow = targetRow + 1

    Next i
End Sub
