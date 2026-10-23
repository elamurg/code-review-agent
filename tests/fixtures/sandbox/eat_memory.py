# Hostile fixture: allocates far more memory than the sandbox allows.
hoard = []
while True:
    hoard.append(bytearray(64 * 1024 * 1024))
