import dataclasses
import logging
import os
import shlex
import subprocess

from typing import Optional

from ktoolbox import firewall
from ktoolbox import host
from ktoolbox.logger import logger


dpu_subnet = "172.131.100.0/24"
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


def nft_masquerade(ifname: str, subnet: str) -> None:
    firewall.nft_call(
        firewall.nft_data_masquerade_up(
            table_name=f"marvell-tools-nat-{ifname}",
            ifname=ifname,
            subnet=subnet,
        )
    )


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


def create_iso_file(iso: str, chroot_path: str) -> str:

    iso0 = iso

    if iso0.startswith("rhel:"):
        rhel_version = iso0[len("rhel:") :]
        if rhel_version == "":
            # This is the default.
            rhel_version = "9.4"
        url = f"https://download.eng.bos.redhat.com/rhel-9/nightly/RHEL-9/latest-RHEL-{rhel_version}.0/compose/BaseOS/aarch64/iso/"
        res = host.local.run(
            f'curl -k -s {shlex.quote(url)} | sed -n \'s/.*href="\\(RHEL-[^"]\\+-dvd1.iso\\)".*/\\1/p\' | head -n1',
            log_level_fail=logging.ERROR,
        )
        url_part = res.out.strip()
        if not res.success or not url_part:
            raise RuntimeError(
                f'failure to detect URL for RHEL ISO image "{iso0}" at URL "{url}"'
            )
        iso1 = f"{url}{url_part}"
    else:
        iso1 = iso0

    if iso1.startswith("http://") or iso1.startswith("https://"):
        filename = iso1[(iso1.rfind("/") + 1) :]
        iso2 = os.path.join(chroot_path, f"root/rhel-iso-{filename}")
        if not os.path.exists(iso2):
            ret = host.local.run(
                f"curl -k -o {shlex.quote(iso2)} {shlex.quote(iso1)}",
                log_level_fail=logging.ERROR,
            )
            if not ret.success:
                raise RuntimeError(
                    f'failure to download RHEL ISO image "{iso1}" to "{iso2}"'
                )
    else:
        iso2 = iso1

    if not os.path.exists(iso2):
        raise RuntimeError(f'iso path "{iso}" ("{iso2}") does not exist')

    logger.info(f"use iso {shlex.quote(iso2)}")
    return iso2
