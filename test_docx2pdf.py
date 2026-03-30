import subprocess
import os

def check_libreoffice():
    commands = [
        "libreoffice",
        "soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    ]
    for cmd in commands:
        try:
            subprocess.run([cmd, "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return cmd
        except Exception:
            continue
    return None

print(check_libreoffice())
