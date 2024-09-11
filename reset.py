#!/usr/bin/env python3

import pexpect
import time

from common_dpu import minicom_cmd
from common_dpu import run


ESC = "\x1b"
KEY_DOWN = "\x1b[B"
KEY_ENTER = "\r\n"


def reset() -> None:
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
    child.sendline("m")
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


def main() -> None:
    reset()


if __name__ == "__main__":
    main()
