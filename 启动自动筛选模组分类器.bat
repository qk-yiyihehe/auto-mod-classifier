@echo off
setlocal
chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
pythonw "%SCRIPT_DIR%自动筛选模组分类器.pyw"
