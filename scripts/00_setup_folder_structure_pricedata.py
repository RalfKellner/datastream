import os
from openpyxl import Workbook

# default for return filtering
# Stock files
#filenames = ["AF", "MTBV", "MV", "P", "PA", "PB", "PH", "PL", "PO", "RI", "STATIC", "UP", "VO"]
# For EU
filenames = ["AF", "MTBV", "MV", "MV_EU", "P", "PA", "PB", "PH", "PL", "PO", "RI", "RI_EU", "STATIC", "UP", "UP_EU", "VO"]

# ETF files
#filenames = ["STATIC", "RI", "DY", "PO", "PH", "PL", "P", "VO", "MV", "AF", "UP", "TER", "TNA", "NAV", "PA", "PB", "NOSH", "PD"]

base_folder = "D:/Datastream/PriceData/EU/"

# 
for i in range(1, 37, 1):
    folder_name = f"{i:02d}"  # Format mit führender Null
    folder_path = os.path.join(base_folder, folder_name)
    try:
        os.makedirs(folder_path, exist_ok=False)
    except:
        print("Folder already exists, moving on!")
    # In jedem Ordner Excel-Dateien erstellen
    for name in filenames:
        file_name = f"{name}_{folder_name}.xlsx"
        file_path = os.path.join(folder_path, file_name)
        wb = Workbook()
        wb.save(file_path)

print("All files and folders have been generated.")