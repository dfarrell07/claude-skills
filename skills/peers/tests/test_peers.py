#!/usr/bin/env python3
"""Offline tests for peers.py using a fake HOME."""
import json
import os
import socket
import subprocess
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
SCRIPT = os.path.join(SCRIPTS, "peers.py")


def start_of(pid):
    with open(f"/proc/{pid}/stat") as f:
        data = f.read()
    return data[data.rindex(")") + 2:].split()[19]


def run(home, *args, config_dir=""):
    env = {"HOME": home, "PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": config_dir}
    return subprocess.run([sys.executable, "-I", SCRIPT, *args], env=env,
                          capture_output=True, text=True)


def write(home, prof, items):
    os.makedirs(os.path.join(home, prof, "sessions"), exist_ok=True)
    for i, r in enumerate(items):
        with open(os.path.join(home, prof, "sessions", f"{i}.json"), "w") as f:
            f.write(r if isinstance(r, str) else json.dumps(r))


def main():
    with tempfile.TemporaryDirectory() as home:
        # Bind by relative path: AF_UNIX paths are short, TMPDIR may be long.
        old = os.getcwd()
        os.chdir(home)
        srv = socket.socket(socket.AF_UNIX)
        srv.bind("s.sock")
        os.chdir(old)
        sock = os.path.join(home, "s.sock")
        me = os.getpid()
        good = {"pid": me, "procStart": start_of(me), "name": "per2-live",
                "status": "idle", "messagingSocketPath": sock, "cwd": "/w"}
        bad = dict(good)
        del bad["procStart"]
        write(home, ".claude-per2", [
            good,
            dict(good, name="reused", procStart="1"),
            dict(bad, name="nostart"),
            dict(good, name="nosock", messagingSocketPath=home + "/missing.sock"),
            dict(good, name="nullsock", messagingSocketPath=None),
            dict(good, name="boolpid", pid=True),
        ])
        write(home, ".claude", [dict(good, name="dup"),
                                dict(good, name="dead", pid=2**22 + 7),
                                "{not json", "[]", "[" * 200000])
        write(home, ".claude-tmp", [dict(good, name="dup")])
        os.symlink(os.path.join(home, ".claude-per2"), os.path.join(home, ".claude-link"))
        write(home, "custom", [dict(good, name="custom", status="x\ny")])

        out = run(home)
        assert out.returncode == 0, out.stderr
        names = sorted(line.split("\t")[0] for line in out.stdout.splitlines())
        assert names == ["dup", "dup", "per2-live"], names
        assert "per2-live\t.claude-per2\t" in out.stdout, out.stdout

        assert "uds:" + sock + "\t/w" in out.stdout, out.stdout
        for name in ("reused", "nostart", "nosock", "nullsock", "boolpid", "dead"):
            assert name not in out.stdout, name
        assert run(home, "bogus").returncode == 2

        out = run(home, config_dir=os.path.join(home, "custom"))
        rows = out.stdout.splitlines()
        assert len(rows) == 4 and rows[0].split("\t")[:3] == ["custom", "custom", "x?y"], rows
        srv.close()
    print("peers tests passed")


if __name__ == "__main__":
    main()
