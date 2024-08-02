import os
import shlex
import subprocess
import dataclasses

from typing import Optional

from ktoolbox import host


dpu_ip4addr = "172.131.100.100"
dpu_ip4addrnet = f"{dpu_ip4addr}/24"
host_ip4addr = "172.131.100.1"
host_ip4addrnet = f"{host_ip4addr}/24"


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


def packaged_file(relative_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def nmcli_setup_mngtiface(
    ifname: str,
    chroot_path: Optional[str],
    ip4addr: str,
) -> None:
    """
    Setup the management interface with a static IP address. For that, ensure we have
    such a connection profile f"{ifname}-marvell-dpu" in NetworkManager. Configure static IP addresses.
    """
    chroot_prefix = ""
    if chroot_path is not None:
        chroot_prefix = f"chroot {shlex.quote(chroot_path)} "
    con_name = f"{ifname}-marvell-dpu"
    res = host.local.run(
        f"{chroot_prefix}nmcli -g connection.uuid connection show id {shlex.quote(con_name)}"
    )
    if not res.success:
        host.local.run(
            f"{chroot_prefix}nmcli connection add type ethernet con-name {shlex.quote(con_name)} ifname {shlex.quote(ifname)} ipv4.method manual ipv4.addresses {shlex.quote(ip4addr)} ipv6.method link-local ipv6.addr-gen-mode eui64",
            die_on_error=True,
        )
        con_spec = f"id {shlex.quote(con_name)}"
    else:
        uuid = res.out.split()[0]
        con_spec = f"uuid {shlex.quote(uuid)}"
        host.local.run(
            f"{chroot_prefix}nmcli connection modify {con_spec} con-name {shlex.quote(con_name)} ifname {shlex.quote(ifname)} ipv4.method manual ipv4.addresses {shlex.quote(ip4addr)} ipv6.method link-local ipv6.addr-gen-mode eui64",
            die_on_error=True,
        )
    host.local.run(f"{chroot_prefix}nmcli connection up {con_spec}", die_on_error=True)


def ssh_generate_key(chroot_path: str) -> str:
    file = f"{chroot_path}/root/.ssh/id_ed25519"
    if not os.path.exists(file) or not os.path.exists(f"{file}.pub"):
        try:
            os.mkdir(os.path.dirname(file))
        except FileExistsError:
            pass
        host.local.run(
            f'ssh-keygen -t ed25519 -C marvell-tools@local.local -N "" -f {shlex.quote(file)}',
            die_on_error=True,
        )
    return file


def ssh_read_pubkey(ssh_privkey_file: str) -> str:
    ssh_pubkey_file = f"{ssh_privkey_file}.pub"
    with open(ssh_pubkey_file, "r") as f:
        ssh_pubkey = f.read()
    for s in ssh_pubkey.splitlines():
        s = s.strip()
        if s:
            return s
    raise RuntimeError('failure to read SSH public key from "{ssh_pubkey_file}"')
