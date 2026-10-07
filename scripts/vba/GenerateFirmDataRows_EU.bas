Attribute VB_Name = "GenerateFirmDataRows_EU"
Option Explicit

' Fills the Datastream request table with one row per (Worldscope variable, LSEG list).
' Monthly data in local currency (no ~E): an EUR conversion would change the values every month
' between reports and break the report-month detection of the baseline panel.
' Destination: <ROOT>\<VAR>\[<VAR>_<nn>.xlsx]Sheet  (templates from 13_setup_folders_firmdata.py)
' WC05350 (fiscal year end) is static and is downloaded separately into the Static subfolder.

Sub GenerateFirmDataRows_EU()

    '------------------------------------------------------------
    '  SET-UP
    '------------------------------------------------------------
    Const ROOT As String = "D:\Datastream\Firmcharacteristics_Monthly\EU\"
    Const LIST_PREFIX As String = "L#EU"      ' list names L#EU001 ... L#EU036
    Const N_LISTS As Long = 36
    Const FILE_DIGITS As String = "00"         ' file numbering: WC01001_01.xlsx (setup script default)
    Const LIST_DIGITS As String = "000"        ' list numbering: L#EU001
    Const START_DATE As String = "1990-01-01"  ' same start as the EU price data
    Const FREQ As String = "Monthly"
    Const DECIMALS As Long = 6                 ' DPL# precision (see note at the end)
    Const SHEET_NAME As String = "Sheet"       ' default sheet name of the empty templates

    Dim ws As Worksheet
    Dim targetRow As Long, colOffset As Long, i As Long
    Dim var As Variant, varList As Variant
    Dim spec As String, fileNo As String

    Set ws = ActiveSheet
    targetRow = 7                              ' first output row
    colOffset = 1                              ' start in column B

    ' Worldscope time series of the baseline panel (config/firm_variables.csv)
    varList = Array("WC01001", "WC01250", "WC01251", "WC01751", "WC18198", _
                    "WC02003", "WC02999", "WC03051", "WC03251", "WC03255", "WC03501", _
                    "WC04601", _
                    "WC08231", "WC08301", "WC08326")

    Application.ScreenUpdating = False

    For Each var In varList                    ' grouped by variable: easy to check one variable at a time
        For i = 1 To N_LISTS

            spec = "DPL#(X(" & var & ")," & DECIMALS & ")"
            fileNo = Format$(i, FILE_DIGITS)

            With ws
                .Cells(targetRow, 1 + colOffset).Value = "NO"
                .Cells(targetRow, 2 + colOffset).Value = "TSL"
                .Cells(targetRow, 3 + colOffset).Value = "RCM$"
                .Cells(targetRow, 4 + colOffset).Value = LIST_PREFIX & Format$(i, LIST_DIGITS)
                .Cells(targetRow, 5 + colOffset).Value = spec
                .Cells(targetRow, 6 + colOffset).Value = START_DATE
                .Cells(targetRow, 7 + colOffset).ClearContents      ' end date: latest
                .Cells(targetRow, 8 + colOffset).Value = FREQ
                .Cells(targetRow, 9 + colOffset).ClearContents
                .Cells(targetRow, 10 + colOffset).Formula = _
                    "='" & ROOT & var & "\[" & var & "_" & fileNo & ".xlsx]" & SHEET_NAME & "'!R1C1"
            End With

            targetRow = targetRow + 1
        Next i
    Next var

    Application.ScreenUpdating = True
    MsgBox (targetRow - 7) & " rows generated (" & (UBound(varList) + 1) & " variables x " & N_LISTS & " lists).", _
           vbInformation

End Sub

' Note on DPL#: all variables use DPL#(X(...),6).
' - Worldscope level items are in thousands of the local currency. Pre-euro values were converted at the
'   fixed conversion rates (e.g. DEM / 1.95583) and therefore have many decimals; small firms have values
'   below 1 (thousand). Without DPL# the add-in may round these.
' - Ratios (WC08231/08301/08326) do not need the extra precision, but it does no harm and keeps one
'   request format for all variables. The importer parses "DPL#(<DSCD>(<VAR>),6)" headers.
