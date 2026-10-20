"""Stop only recorded application process trees, guarding against PID reuse."""
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
record_path = ROOT/'data/.runtime/app-processes.json'
if record_path.exists():
    record = json.loads(record_path.read_text(encoding='utf-8-sig'))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for item in reversed(record.get('processes', [])):
        if item.get('name') not in ('data-api','graph','web','retrieval'):
            continue
        handle = kernel.OpenProcess(0x1000, False, int(item['id']))
        if not handle:
            continue
        times = [wintypes.FILETIME() for _ in range(4)]
        try:
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                continue
            actual = ((times[0].dwHighDateTime << 32) + times[0].dwLowDateTime)/10_000_000 - 11644473600
            expected = datetime.fromisoformat(item['started'].replace('Z','+00:00')).timestamp()
            if abs(actual-expected) >= 2:
                print(f"Skipped reused PID {item['id']}")
                continue
        finally:
            kernel.CloseHandle(handle)
        result = subprocess.run(['taskkill','/PID',str(item['id']),'/T','/F'], capture_output=True)
        print(f"Stopped {item['name']} ({item['id']}), exit={result.returncode}")
    record_path.write_text('{}', encoding='utf-8')
