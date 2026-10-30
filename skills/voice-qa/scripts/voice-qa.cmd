@echo off
setlocal DisableDelayedExpansion
if defined VOICEQA_PYTHON goto configured
where py >nul 2>&1
if not errorlevel 1 goto py_launcher
where python >nul 2>&1
if not errorlevel 1 goto python_launcher
echo voice-qa: Python is required; set VOICEQA_PYTHON to its absolute path. 1>&2
exit /b 2
:configured
"%VOICEQA_PYTHON%" "%~dp0launch.py" %*
exit /b %errorlevel%
:py_launcher
py -3 "%~dp0launch.py" %*
exit /b %errorlevel%
:python_launcher
python "%~dp0launch.py" %*
exit /b %errorlevel%
