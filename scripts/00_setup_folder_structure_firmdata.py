import os
from openpyxl import Workbook

# default for return filtering
var_name = "ENERDP024"
base_folder = "D:/Datastream/Firmcharacteristics_Monthly/US/"

# 
for i in range(1, 33, 1):
    folder_name = f"{i:03d}"  # Format mit führender Null
    folder_path = os.path.join(base_folder, var_name)
    if i == 1:
        try:
            os.makedirs(folder_path, exist_ok=True)
        except:
            print("Folder already exists!")
            break
    # In jedem Ordner Excel-Dateien erstellen
    file_name = f"{var_name}_{folder_name}.xlsx"
    file_path = os.path.join(folder_path, file_name)
    wb = Workbook()
    wb.save(file_path)

print("All files and folders have been generated.")