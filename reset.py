#!/usr/bin/env python

import time

from ktoolbox.logger import logger
from ktoolbox import common

import common_dpu

from common_dpu import KEY_CTRL_M


def _reset(try_idx: int, retry_count: int) -> None:
    logger.debug(f"serial: reset {common_dpu.TTYUSB1} (try {try_idx} of {retry_count})")
    with common.Serial(common_dpu.TTYUSB1) as ser:
        time.sleep(1)
        ser.send(KEY_CTRL_M * 2)
        ser.expect("SCP Main Menu")
        ser.send("m" + KEY_CTRL_M)
        ser.expect("SCP Management Menu")
        ser.send("r" + KEY_CTRL_M, sleep=0.5)
        buffer = ser.read_all()
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


def main() -> None:
    reset()


if __name__ == "__main__":
    main()
