#!/usr/bin/env python
"""Saleae connectivity test with full diagnostics."""
import sys, os, traceback, subprocess

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

print(f"Python: {sys.executable}")
print(f"Script dir: {os.path.dirname(os.path.abspath(__file__))}")
print(f"PID: {os.getpid()}")
print()

# Check typing_extensions
try:
    import typing_extensions
    print("[1/4] typing_extensions: OK")
except Exception as e:
    print(f"[1/4] typing_extensions: MISSING - {e}")
    print("  Installing...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'typing_extensions', '--quiet'])

# Check google.protobuf
try:
    import google.protobuf
    print(f"[2/4] google.protobuf: OK, v{google.protobuf.__version__}")
except Exception as e:
    print(f"[2/4] google.protobuf: FAIL - {e}")
    traceback.print_exc()

# Check grpc
try:
    import grpc
    print(f"[3/4] grpc: OK, v{grpc.__version__}")
except Exception as e:
    print(f"[3/4] grpc: FAIL - {e}")
    traceback.print_exc()

# Check saleae
try:
    from saleae import automation
    print("[4/4] saleae: OK")
    m = automation.Manager.connect(port=10430)
    devs = m.get_devices()
    print(f"  Connected! Device: {devs[0].device_id}")
except Exception as e:
    print(f"[4/4] saleae: FAIL - {e}")
    traceback.print_exc()

print()
print("Done. Press Enter to exit...")
input()
