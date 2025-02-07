#!/usr/bin/env python3

import argparse
import os
import shlex
import shutil
import time
import typing

from collections.abc import Iterable

from ktoolbox import common
from ktoolbox import host

import common_dpu

from common_dpu import KEY_ENTER
from common_dpu import logger
from common_dpu import run_process
from reset import reset


DEFAULT_IMG_UBOOT = (
    "http://file.brq.redhat.com/~thaller/marvell-sdk/flash-cn10ka-SDK12.25.01.img"
)
DEFAULT_IMG_UEFI = (
    "http://file.brq.redhat.com/~thaller/marvell-sdk/flash-uefi-cn10ka-12.25.01.img"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process FW IMG file.")
    parser.add_argument(
        "img",
        type=str,
        nargs="?",
        default=None,
        help=f'IMG file with firmware. For path names, make sure the path is reachable from inside the container (e.g. run `podman -v /:/host`). This can also be a HTTP/HTTPS URL. The special words "uboot" (for "{DEFAULT_IMG_UBOOT}" and "uefi" (for "{DEFAULT_IMG_UEFI}") are supported. The default depends on "--boot-device" and is "uboot" (for "primary") or "uefi" (for "secondary").',
    )
    parser.add_argument(
        "--dev",
        type=str,
        default="eno4",
        help="Optional argument of type string for device. Default is 'eno4'.",
    )
    parser.add_argument(
        "-P",
        "--prompt",
        action="store_true",
        help="If set, start DHCP/TFTP/HTTP services and wait for the user to press ENTER. This can be used to manually setup the host side serving the firmware.",
    )
    parser.add_argument(
        "-B",
        "--boot-device",
        choices=["1", "2", "primary", "secondary"],
        default="secondary",
        help='Select primary or secondary boot device. Defaults to "secondary".',
    )

    args = parser.parse_args()

    if args.boot_device == "1":
        args.boot_device = "primary"
    elif args.boot_device == "2":
        args.boot_device = "secondary"

    return args


def prepare_image(boot_device: str, img: typing.Optional[str]) -> str:
    if not img:
        if boot_device == "primary":
            img = "uboot"
        else:
            img = "uefi"

    if img == "uboot":
        img = DEFAULT_IMG_UBOOT
    elif img == "uefi":
        img = DEFAULT_IMG_UEFI

    if img.startswith("http://") or img.startswith("https://"):
        img2 = "/tmp/fwupdate.img"
        logger.info(f"downloading {repr(img)} to {repr(img2)}.")
        host.local.run(
            ["curl", "-k", "-L", "-o", img2, img],
            die_on_error=True,
        )
        img = img2
    else:
        logger.info(f"using image {repr(img)}.")

    if not os.path.exists(img):
        logger.error(f"Couldn't find img file {shlex.quote(img)}")
        raise Exception(f"Invalid image path {shlex.quote(img)}")
    return img


def wait_any_ping(hn: Iterable[str], timeout: float) -> str:
    logger.info("Waiting for response from ping")
    begin = time.time()
    end = begin
    hn = list(hn)
    while end - begin < timeout:
        for e in hn:
            if common_dpu.ping(e):
                return e
        time.sleep(5)
        end = time.time()
    raise Exception(f"No response after {round(end - begin, 2)}s")


def firmware_update(img_path: str, boot_device: str) -> None:
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
        logger.info("Press ENTER for uboot menu")
        ser.send(KEY_ENTER)
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
        logger.info(f"set to {boot_device} SPI flash")
        if boot_device == "primary":
            ser.send("sf probe 0:0")
        else:
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
    logger.info("Configuring TFTP")
    os.makedirs("/var/lib/tftpboot", exist_ok=True)
    logger.info("starting in.tftpd")
    host.local.run("killall in.tftpd")
    run_process("tftpd", "/usr/sbin/in.tftpd -s -B 1468 -L /var/lib/tftpboot")
    shutil.copy(f"{img}", "/var/lib/tftpboot")


def setup_dhcp(dev: str) -> None:
    logger.info("Configuring DHCP")
    host.local.run(f"ip addr add 172.131.100.1/24 dev {shlex.quote(dev)}")
    shutil.copy(
        common_dpu.packaged_file("manifests/pxeboot/dhcpd.conf"),
        "/etc/dhcp/dhcpd.conf",
    )
    host.local.run("killall dhcpd")
    run_process(
        "dhcpd",
        "/usr/sbin/dhcpd -f -cf /etc/dhcp/dhcpd.conf -user dhcpd -group dhcpd",
    )


def main() -> None:
    args = parse_args()
    img = prepare_image(args.boot_device, args.img)
    logger.info("Preparing services for FW update")
    setup_dhcp(args.dev)
    setup_tftp(img)
    logger.info("Giving services time to settle")
    time.sleep(3)

    common_dpu.check_services_running()

    if args.prompt:
        input(
            "dhcp/tftp/http services started. Waiting. Press ENTER to continue or abort with CTRL+C"
        )

    logger.info("Starting FW Update")
    logger.info("Resetting card")
    reset()
    firmware_update(img, args.boot_device)
    logger.info("Terminating http, tftp, and dhcpd")
    common.thread_list_join_all()


if __name__ == "__main__":
    main()
