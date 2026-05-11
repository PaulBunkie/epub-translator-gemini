@echo off
echo Deleting video_analyzer.db on Fly.io volume...
fly ssh console -C "rm -f /data/video_analyzer.db /data/video_analyzer.db-shm /data/video_analyzer.db-wal"
if %ERRORLEVEL% EQU 0 (
    echo Successfully deleted.
) else (
    echo Error occurred during deletion.
)
pause
