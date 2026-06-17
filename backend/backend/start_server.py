import subprocess
import sys

with open("server.log", "w") as f:
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.main:backend_app", "--port", "8000"],
        stdout=f,
        stderr=subprocess.STDOUT
    )
    process.wait()
