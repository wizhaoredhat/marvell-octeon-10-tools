import os
import shlex
import subprocess
import dataclasses


def minicom_cmd(device: str) -> str:
    return f"minicom -D {device}"


@dataclasses.dataclass(frozen=True)
class Result:
    out: str
    err: str
    returncode: int


def run(cmd: str, env: dict[str, str] = os.environ.copy()) -> Result:
    print(f"running {cmd}")
    args = shlex.split(cmd)
    res = subprocess.run(
        args,
        capture_output=True,
        env=env,
    )

    result = Result(
        out=res.stdout.decode("utf-8"),
        err=res.stderr.decode("utf-8"),
        returncode=res.returncode,
    )

    print(f"Result: {result.out}\n{result.err}\n{result.returncode}\n")
    return result
