@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
set "RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-msvc"
set "PATH=C:\Program Files\nodejs;%PATH%"
cd /d "C:\Bot mk3\ui"
npm run tauri dev
