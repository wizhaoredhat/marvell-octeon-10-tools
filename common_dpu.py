from collections import namedtuple
import subprocess
import os
import shlex

def minicom_cmd(device):
    return f'minicom -D {device}'

def run(cmd: str, env: dict = os.environ.copy()):
    print(f"running {cmd}")
    Result = namedtuple("Result", "out err returncode")
    args = shlex.split(cmd)
    pipe = subprocess.PIPE
    with subprocess.Popen(args, stdout=pipe, stderr=pipe, env=env) as proc:
        out = proc.stdout.read().decode("utf-8")
        err = proc.stderr.read().decode("utf-8")
        proc.communicate()
        ret = proc.returncode
    print(f"Result: {Result.out}\n{Result.err}\n{ret}\n")
    return Result(out, err, ret)
