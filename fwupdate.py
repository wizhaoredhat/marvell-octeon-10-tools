#!/usr/bin/env python3

import argparse
import os
import pexpect
import shutil
import time

from collections.abc import Iterable
from multiprocessing import Process

import common_dpu

from common_dpu import minicom_cmd
from common_dpu import run
from reset import reset


children = []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process FW IMG file.")
    parser.add_argument(
        "img",
        type=str,
        help="Mandatory argument of type string for IMG file (make sure the path to this file was mounted when running the pod via -v /host/img:/container/img).",
    )
    parser.add_argument(
        "--dev",
        type=str,
        default="eno4",
        help="Optional argument of type string for device. Default is 'eno4'.",
    )

    args = parser.parse_args()
    if not os.path.exists(args.img):
        print(f"Couldn't read img file {args.img}")
        raise Exception("Invalid path to omg provided")

    return args


def run_process(cmd: str) -> Process:
    p = Process(target=run, args=(cmd,))
    p.start()
    return p


def wait_any_ping(hn: Iterable[str], timeout: float) -> str:
    print("Waiting for response from ping")
    begin = time.time()
    end = begin
    hn = list(hn)
    while end - begin < timeout:
        for e in hn:
            if ping(e):
                return e
        time.sleep(5)
        end = time.time()
    raise Exception(f"No response after {round(end - begin, 2)}s")


def ping(hn: str) -> bool:
    ping_cmd = f"timeout 1 ping -4 -c 1 {hn}"
    return run(ping_cmd).returncode == 0


ESC = "\x1b"
KEY_DOWN = "\x1b[B"
KEY_ENTER = "\r\n"


def firmware_update(img_path: str) -> None:
    print("firmware updating")
    img = os.path.basename(img_path)

    run("pkill -9 minicom")
    print("spawn minicom")
    child = pexpect.spawn(minicom_cmd("/dev/ttyUSB0"))
    child.maxread = 10000
    print("waiting for instructions to access boot menu")
    child.expect("Press 'B' within 10 seconds for boot menu", 30)
    time.sleep(1)
    print("Pressing B to access boot menu")
    child.send("b")
    print("waiting for instructions to Boot from Primary Boot Device")
    child.expect("1\\) Boot from Primary Boot Device", 10)
    time.sleep(1)
    child.send("1")
    print("waiting to escape to uboot menu")
    child.expect("Hit any key to stop autoboot", 60)
    print("Sending escape 5 times")
    child.send(ESC * 5)
    print("waiting on uboot prompt")
    child.expect("crb106-pcie>", 5)
    print("enabling 100G management port")
    child.send("setenv ethact rvu_pf#1")
    child.send(KEY_ENTER)
    time.sleep(3)
    print("saving environment")
    child.send("saveenv")
    child.send(KEY_ENTER)
    child.expect("OK", 10)
    time.sleep(3)
    print("enabling dhcp")
    child.send("dhcp")
    child.send(KEY_ENTER)
    child.expect("DHCP client bound to address", 30)
    time.sleep(1)
    print("set serverip")
    child.send("setenv serverip 172.131.100.1")
    child.send(KEY_ENTER)
    time.sleep(1)
    print("tftp the image")
    child.send(f"tftpboot $loadaddr {img}")
    child.send(KEY_ENTER)
    child.expect("Bytes transferred", 100)
    time.sleep(1)
    print("set to secondary SPI flash")
    child.send("sf probe 1:0")
    child.send(KEY_ENTER)
    child.expect("SF: Detected", 10)
    time.sleep(1)
    print("updating flash!")
    child.send("sf update $fileaddr 0 $filesize")
    child.send(KEY_ENTER)
    child.expect("bytes written", 500)
    time.sleep(1)
    print("reseting")
    child.send("reset")
    child.send(KEY_ENTER)
    child.close()
    print("Closing minicom")


def setup_tftp(img: str) -> None:
    print("Configuring TFTP")
    os.makedirs("/var/lib/tftpboot", exist_ok=True)
    print("starting in.tftpd")
    run("killall in.tftpd")
    p = run_process("/usr/sbin/in.tftpd -s -B 1468 -L /var/lib/tftpboot")
    children.append(p)
    shutil.copy(f"{img}", "/var/lib/tftpboot")


def setup_dhcp(dev: str) -> None:
    print("Configuring DHCP")
    run(f"ip addr add 172.131.100.1/24 dev {dev}")
    shutil.copy(
        common_dpu.packaged_file("manifests/pxeboot/dhcpd.conf"),
        "/etc/dhcp/dhcpd.conf",
    )
    run("killall dhcpd")
    p = run_process(
        "/usr/sbin/dhcpd -f -cf /etc/dhcp/dhcpd.conf -user dhcpd -group dhcpd"
    )
    children.append(p)


def main() -> None:
    args = parse_args()
    print("Preparing services for FW update")
    setup_dhcp(args.dev)
    setup_tftp(args.img)
    print("Giving services time to settle")
    time.sleep(10)
    print("Starting FW Update")
    print("Resetting card")
    reset()
    firmware_update(args.img)
    print("Terminating http, tftp, and dhcpd")
    for e in children:
        e.terminate()


if __name__ == "__main__":
    main()
