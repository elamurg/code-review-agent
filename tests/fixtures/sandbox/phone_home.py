# Hostile fixture: tries to send data out. With no network it must fail.
import socket

try:
    socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    print("CONNECTED")
except OSError as exc:
    print(f"BLOCKED {type(exc).__name__}")
