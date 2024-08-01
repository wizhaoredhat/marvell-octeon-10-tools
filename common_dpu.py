from collections import namedtuple
import subprocess
import os
import shlex


def minicom_cmd(device):
    return f"minicom -D {device}"


Result = namedtuple("Result", "out err returncode")


def run(cmd: str, env: dict = os.environ.copy()):
    print(f"running {cmd}")
    args = shlex.split(cmd)
    res = subprocess.run(
        args,
        capture_output=True,
        env=env,
    )

    result = Result(
        res.stdout.decode("utf-8"), res.stderr.decode("utf-8"), res.returncode
    )

    print(f"Result: {result.out}\n{result.err}\n{result.returncode}\n")
    return result
