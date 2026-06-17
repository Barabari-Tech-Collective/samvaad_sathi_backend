import sys
import subprocess
import os

output = []
output.append(f"Python executable: {sys.executable}")
output.append(f"Python version: {sys.version}")
output.append(f"CWD: {os.getcwd()}")
output.append("")

# Check if authlib is importable
try:
    import authlib
    output.append(f"authlib FOUND at: {authlib.__file__}")
except ImportError as e:
    output.append(f"authlib NOT FOUND: {e}")

output.append("")

# Check pip show
result = subprocess.run(
    [sys.executable, "-m", "pip", "show", "authlib"],
    capture_output=True, text=True
)
output.append(f"pip show authlib stdout: {result.stdout}")
output.append(f"pip show authlib stderr: {result.stderr}")

# Write to file
with open("debug_output.txt", "w") as f:
    f.write("\n".join(output))

print("Debug output written to debug_output.txt")
