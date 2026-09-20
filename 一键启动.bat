@echo off
chcp 936 >nul
title 嘉庚智慧馆员 · 一键启动
echo ============================================
echo   嘉庚智慧馆员 Agent · 一键启动
echo ============================================
echo.

set "DEMO=%~dp0demo"
if not exist "%DEMO%\app.py" set "DEMO=%~dp0"
cd /d "%DEMO%"

set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=

REM 选择可用的 Python：优先 python，其次 py 启动器
set "PY="
python -c "print(1)" >nul 2>nul
if %errorlevel% equ 0 set "PY=python"
if not defined PY (
    py -3 -c "print(1)" >nul 2>nul
    if %errorlevel% equ 0 set "PY=py -3"
)
if not defined PY (
    echo [错误] 未检测到可用的 Python。
    echo 请先安装 Python 3.9 以上版本，安装时务必勾选 "Add Python to PATH"。
    echo 下载地址：https://www.python.org/downloads/
    echo 安装完成后重新双击本文件。
    pause
    exit /b 1
)
echo       使用解释器：%PY%

REM 检查依赖
echo [1/4] 检查依赖...
%PY% -c "import flask,pandas,openpyxl,numpy" >nul 2>nul
if %errorlevel% neq 0 (
    echo       首次运行，正在安装依赖，约1-2分钟，请稍候...
    if exist "%DEMO%\requirements.txt" (
        %PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    ) else (
        %PY% -m pip install flask pandas openpyxl numpy -i https://pypi.tuna.tsinghua.edu.cn/simple
    )
    %PY% -c "import flask,pandas,openpyxl,numpy" >nul 2>nul
    if %errorlevel% neq 0 (
        echo [错误] 依赖安装失败，请检查网络后重试，或手动执行：
        echo       %PY% -m pip install flask pandas openpyxl numpy
        pause
        exit /b 1
    )
)

REM 端口占用检测
echo [2/4] 检查 8000 端口...
curl -s --noproxy "*" -o nul http://127.0.0.1:8000/ 2>nul
if %errorlevel% equ 0 (
    echo       服务已在运行，直接打开浏览器...
    start http://127.0.0.1:8000
    exit /b 0
)

REM 启动服务
echo [3/4] 启动服务，首次加载45万册馆藏约需10-20秒...
start "嘉庚智慧馆员服务" %PY% app.py

echo [4/4] 等待服务就绪...
set /a cnt=0
:waitloop
ping 127.0.0.1 -n 3 >nul
curl -s --noproxy "*" -o nul http://127.0.0.1:8000/ 2>nul
if %errorlevel% equ 0 goto ready
set /a cnt+=1
if %cnt% lss 20 goto waitloop
echo [提示] 服务启动较慢，浏览器将先打开，若未显示请稍等几秒后刷新
goto open
:ready
echo       服务已就绪。
:open
start http://127.0.0.1:8000
echo.
echo ============================================
echo   启动完成，地址 http://127.0.0.1:8000
echo   关闭"嘉庚智慧馆员服务"窗口即可停止服务
echo ============================================
pause