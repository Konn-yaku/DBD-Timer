@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================
echo  DBD 下钩计时助手 - 打包成 exe（单文件、无控制台）
echo ============================================

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到虚拟环境，请先运行:
  echo   py -3.11 -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

echo [1/3] 安装 PyInstaller（已安装则自动跳过）...
".venv\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 goto :fail

echo [2/3] 运行冒烟测试（全部通过才继续打包）...
".venv\Scripts\python.exe" tests_smoke.py
if errorlevel 1 goto :fail

echo [3/3] PyInstaller 构建单文件 exe（首次较慢，请稍候）...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean dbdtimer.spec
if errorlevel 1 goto :fail

rem 附带一份面向最终用户的使用说明到产物目录
if exist "dist\使用说明.txt" del /q "dist\使用说明.txt" >nul 2>nul
if exist "发布说明.txt" copy /y "发布说明.txt" "dist\使用说明.txt" >nul

echo.
echo 完成！产物: dist\DBD下钩计时助手.exe
echo 分发时请连同 dist\使用说明.txt 一起打包（config.json / templates 由程序首次运行时在 exe 旁自动生成）。
pause
exit /b 0

:fail
echo.
echo [失败] 打包中止，请查看上方错误信息。
pause
exit /b 1
