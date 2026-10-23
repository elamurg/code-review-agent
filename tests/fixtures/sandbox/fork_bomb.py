# Hostile fixture: forks forever and ignores failures. The pids limit must cap it and
# the timeout must kill it.
import os

while True:
    try:
        os.fork()
    except OSError:
        pass
