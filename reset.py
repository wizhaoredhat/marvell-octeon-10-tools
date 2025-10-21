#!/usr/bin/env python3

import argparse
import re
import time

from typing import Optional

from ktoolbox import common

import common_dpu

from common_dpu import KEY_CTRL_M
from common_dpu import logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset/reboot Marvell DPU.\n\n"
        f"Connects to {common_dpu.TTYUSB1} to reset the DPU. Note that this might not work, if the DPU hangs in early boot. In that case, manually connect to {common_dpu.TTYUSB0} and resolve the problem.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-B",
        "--boot-device",
        choices=["none", "1", "2", "primary", "secondary"],
        default="none",
        help='If set to "primary"/"secondary", select the requested boot device in the boot menu. Defaults to "none" to skip this.',
    )

    args = parser.parse_args()

    if args.boot_device in ("1", "primary"):
        args.boot_device = 1
    elif args.boot_device in ("2", "secondary"):
        args.boot_device = 2
    else:
        args.boot_device = None

    return args


def _reset(try_idx: int, retry_count: int) -> None:
    logger.debug(f"serial: reset {common_dpu.TTYUSB1} (try {try_idx} of {retry_count})")
    with common.Serial(common_dpu.TTYUSB1) as ser:
        for i in range(10):
            time.sleep(1)
            ser.send(KEY_CTRL_M * 2)
            b1 = ser.expect("uart:|SCP Main Menu")
            if re.search("SCP Main Menu", b1):
                ser.send("m" + KEY_CTRL_M)
                ser.expect("SCP Management Menu")
                ser.send("r" + KEY_CTRL_M, sleep=0.5)
                buffer = ser.read_all()
                break
            elif re.search("uart:", b1):
                ser.send("kernel reboot warm" + KEY_CTRL_M, sleep=0.05)
                buffer = ser.read_all()
                break
            else:
                continue
        else:
            raise RuntimeError(f"Error rebooting DPU via {common_dpu.TTYUSB1}")
        logger.debug(
            f"serial[{ser.port}]: reset complete (buffer content {repr(buffer)})"
        )


def reset(retry_count: int = 5) -> None:
    try_idx = 0
    while True:
        try:
            _reset(try_idx, retry_count)
        except Exception as e:
            logger.debug(f"serial: reset failed: {e}")
            if try_idx + 1 == retry_count:
                raise
            try_idx += 1
            logger.debug("serial: retry in 5 seconds")
            time.sleep(5)
            continue
        return


def select_boot_device(boot_device: Optional[int]) -> None:

    if boot_device is None:
        return

    logger.info("selecting pxe entry")

    with common.Serial(common_dpu.TTYUSB0) as ser:

        while True:

            found = ser.expect(re.compile("Boot: .*using SPI[01]_CS0"))

            current = 1 if found.endswith("SPI0_CS0") else 2

            if current == boot_device:
                return

            ser.expect("Press 'B' within 10 seconds for boot menu", 30)
            ser.send("b")

            ser.expect("2\\) Boot from Secondary Boot Device", 10)
            ser.send(str(boot_device))


def main() -> None:
    args = parse_args()
    reset()
    select_boot_device(args.boot_device)


if __name__ == "__main__":
    common_dpu.run_main(main)
