# Hostile fixture: tries to modify the checkout. The mount is read-only.
try:
    with open("planted.py", "w") as handle:
        handle.write("print('owned')\n")
    print("WROTE")
except OSError as exc:
    print(f"BLOCKED {type(exc).__name__}")
