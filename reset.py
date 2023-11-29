import argparse

from collections import namedtuple
import subprocess
import os
import sys
import shlex
import pexpect
import time

ESC = "\x1b"
KEY_DOWN = '\x1b[B'
KEY_ENTER = '\r\n'

def run(cmd: str, env: dict = os.environ.copy()):
    Result = namedtuple("Result", "out err returncode")
    args = shlex.split(cmd)
    pipe = subprocess.PIPE
    with subprocess.Popen(args, stdout=pipe, stderr=pipe, env=env) as proc:
        out = proc.stdout.read().decode("utf-8")
        err = proc.stderr.read().decode("utf-8")
        proc.communicate()
        ret = proc.returncode
    return Result(out, err, ret)

def minicom_cmd(device):
    return f'minicom -D {device}'

def reset():
    run("pkill -9 minicom")
    print("spawn minicom")
    child = pexpect.spawn(minicom_cmd("/dev/ttyUSB1"))
    child.maxread = 10000
    print("waiting for minicom startup menu")
    child.expect("Welcome to minicom", timeout=3)
    time.sleep(1)
    print("pressing enter")
    child.send(KEY_ENTER)
    child.sendcontrol("m")
    time.sleep(1)
    print("Waiting on SCP Main Menu")
    child.expect("SCP Main Menu", timeout=3)
    child.sendline('m')
    time.sleep(1)
    child.sendcontrol("m")
    time.sleep(1)
    print("Waiting on SCP Management Menu")
    child.expect("SCP Management Menu", timeout=3)
    child.sendline("r")
    time.sleep(1)
    child.sendcontrol("m")
    time.sleep(1)
    child.close()

def main():
    reset()

if __name__ == "__main__":
    main()
