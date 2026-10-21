# Deliberately broken: every analyser should find something in here.
# Excluded from this project's own ruff and mypy runs (see pyproject.toml).
import os
import pickle
import subprocess

import requests

DB_PASSWORD = "hunter2-not-a-real-password"


def run(command):
    return subprocess.call(command, shell=True)


def load(blob):
    return pickle.loads(blob)


def fetch(url):
    return requests.get(url, verify=False, timeout=5)


def lookup(cursor, name):
    cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cursor.fetchone()


def total(items: list[int]) -> str:
    return sum(items) + undefined_offset
