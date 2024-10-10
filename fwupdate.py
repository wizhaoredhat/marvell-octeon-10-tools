#!/usr/bin/env python3

import argparse
import os
import shutil
import time

from collections.abc import Iterable
from multiprocessing import Process

from ktoolbox import common

import common_dpu

from common_dpu import ESC
from common_dpu import KEY_ENTER
from common_dpu import logger
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


def firmware_update(img_path: str) -> None:
    img = os.path.basename(img_path)
    logger.info(f"firmware updating (image {repr(img)})")

    with common.Serial(common_dpu.TTYUSB0) as ser:
        logger.info("waiting for instructions to access boot menu")
        ser.expect("Press 'B' within 10 seconds for boot menu", 30)
        time.sleep(1)
        logger.info("Pressing B to access boot menu")
        ser.send("b")
        logger.info("waiting for instructions to Boot from Primary Boot Device")
        ser.expect("1\\) Boot from Primary Boot Device", 10)
        time.sleep(1)
        ser.send("1")
        logger.info("waiting to escape to uboot menu")
        ser.expect("Hit any key to stop autoboot", 60)
        logger.info("Sending escape 5 times")
        ser.send(ESC * 5)
        logger.info("waiting on uboot prompt")
        ser.expect("crb106-pcie>", 5)
        logger.info("enabling 100G management port")
        ser.send("setenv ethact rvu_pf#1")
        ser.send(KEY_ENTER)
        time.sleep(3)
        logger.info("saving environment")
        ser.send("saveenv")
        ser.send(KEY_ENTER)
        ser.expect("OK", 10)
        time.sleep(3)
        logger.info("enabling dhcp")
        ser.send("dhcp")
        ser.send(KEY_ENTER)
        ser.expect("DHCP client bound to address", 30)
        time.sleep(1)
        logger.info("set serverip")
        ser.send("setenv serverip 172.131.100.1")
        ser.send(KEY_ENTER)
        time.sleep(1)
        logger.info("tftp the image")
        ser.send(f"tftpboot $loadaddr {img}")
        ser.send(KEY_ENTER)
        ser.expect("Bytes transferred", 100)
        time.sleep(1)
        logger.info("set to secondary SPI flash")
        ser.send("sf probe 1:0")
        ser.send(KEY_ENTER)
        ser.expect("SF: Detected", 10)
        time.sleep(1)
        logger.info("updating flash!")
        ser.send("sf update $fileaddr 0 $filesize")
        ser.send(KEY_ENTER)
        ser.expect("bytes written", 500)
        time.sleep(1)
        logger.info("reseting")
        ser.send("reset")
        ser.send(KEY_ENTER)


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
