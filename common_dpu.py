import logging
import os
import shlex
import shutil
import typing

from collections.abc import Iterable
from typing import Callable
from typing import Optional
from typing import Union

from ktoolbox import common
from ktoolbox import firewall
from ktoolbox import host


dpu_subnet = "172.131.100.0/24"
dpu_ip4addr = "172.131.100.100"
dpu_ip4addrnet = f"{dpu_ip4addr}/24"
host_ip4addr = "172.131.100.1"
host_ip4addrnet = f"{host_ip4addr}/24"

# The subnet range from "manifests/pxeboot/dhcpd.conf"
DPU_DHCPRANGE = tuple(f"172.131.100.{i}" for i in range(10, 20 + 1))

ESC = "\x1b"
KEY_DOWN = "\x1b[B"
KEY_ENTER = "\r\n"
KEY_CTRL_M = "\r"

TTYUSB0 = "/dev/ttyUSB0"
TTYUSB1 = "/dev/ttyUSB1"


logger = common.ExtendedLogger("marvell_toolbox")

common.log_config_logger(logging.DEBUG, logger, "ktoolbox")


def run_process(
    tag: str,
    cmd: Union[str, Iterable[str]],
) -> common.FutureThread[host.Result]:
    return host.local.run_in_thread(
        cmd,
        log_lineoutput=True,
        add_to_thread_list=True,
        user_data=tag,
        check_success=lambda r: r.cancelled,
        log_level_fail=logging.ERROR,
    )


def check_services_running() -> None:
    for th in common.thread_list_get():
        assert isinstance(th, common.FutureThread)
        if th.poll() is None:
            continue
        logger.error_and_exit(
            f"Service {th.user_data} unexpectedly not running. Check logging output!!"
        )


def run_dhcpd(*, pxe_line: Optional[str] = None) -> None:
    logger.info("Configuring DHCP")

    shutil.copy(
        packaged_file("manifests/pxeboot/dhcpd.conf"),
        "/etc/dhcp/dhcpd.conf",
    )

    if not pxe_line:
        pxe_line = "# no pxe options"

    host.local.run(
        [
            "sed",
            "-i",
            f"s/#__PXE_LINE__/{common.sed_escape_repl(pxe_line)}/",
            "/etc/dhcp/dhcpd.conf",
        ]
    )

    host.local.run("killall dhcpd")

    # On CoreOS, br-ex tends to have an address with a "label vip". That trips
    # up dhcpd, which uses legacy API to access addresses (compare legacy
    # `ifconfig` vs `ip addr`).
    #
    # Workaround by deleting and re-adding the address.
    host.local.run(
        "ip addr del 192.168.122.101/32 dev br-ex scope global label vip && ip addr add 192.168.122.101/32 dev br-ex scope global"
    )

    run_process(
        "dhcpd",
        "/usr/sbin/dhcpd -f -cf /etc/dhcp/dhcpd.conf -user dhcpd -group dhcpd",
    )


cwd, basedir = common.path_basedir(__file__)


def packaged_file(relative_path: str) -> str:
    return common.path_norm(basedir + "/" + relative_path)


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


def ssh_generate_key(
    *,
    file: str,
    create: bool = True,
    comment: str = "pxeboot@marvel-tools.local",
) -> Optional[str]:
    assert file
    assert not file.endswith(".pub")
    if not os.path.exists(file) or not os.path.exists(f"{file}.pub"):
        if not create:
            logger.info(f"ssh-generate-key: skip creating key {repr(file)} on host")
            return None
        try:
            os.mkdir(os.path.dirname(file), mode=0o700)
        except FileExistsError:
            pass
        host.local.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-C",
                comment,
                "-N",
                "",
                "-f",
                file,
            ],
            die_on_error=True,
        )
        logger.info(f"ssh-generate-key: SSH key {repr(file)} created")
    else:
        logger.info(f"ssh-generate-key: use existing SSH key {repr(file)}")
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


DEFAULT_RHEL_ISO = "9.6"


def check_files(
    files: Iterable[str],
    *,
    cwd: Optional[str] = None,
    read_check: bool = False,
) -> bool:
    files = common.iter_eval_now(files)
    files = [common.path_norm(s, cwd=cwd) for s in files]

    if not all(os.path.exists(f) for f in files):
        return False

    if read_check and not host.local.run(["sha256sum", *files]):
        return False

    return True


def mount_iso(
    iso_path: str,
    *,
    mount_path: str,
) -> bool:
    os.makedirs(mount_path, exist_ok=True)
    host.local.run(["umount", mount_path])
    ret = host.local.run(
        ["mount", "-o", "loop", iso_path, mount_path],
        log_level_fail=logging.WARN,
    )
    return ret.success


def create_iso_file(
    iso: str,
    chroot_path: str,
    *,
    force: bool = False,
) -> tuple[str, Optional[str], bool]:

    cached_http_file = False
    iso_url: Optional[str] = None

    iso0 = iso

    # Is iso0 a "rhel:" URL? In that case, resolve the corresponding HTTP/HTTPS URL.
    # Assign the result to "iso1".
    if iso0.startswith("rhel:"):
        rhel_version = iso0[len("rhel:") :]
        if rhel_version == "":
            # This is the default.
            rhel_version = DEFAULT_RHEL_ISO
        url = f"https://download.eng.brq.redhat.com/rhel-9/nightly/RHEL-9/latest-RHEL-{rhel_version}/compose/BaseOS/aarch64/iso/"
        res = host.local.run(
            f'curl -L -k -s {shlex.quote(url)} | sed -n \'s/.*href="\\(RHEL-[^"]\\+-dvd1.iso\\)".*/\\1/p\' | head -n1',
            log_level_fail=logging.ERROR,
        )
        url_part = res.out.strip()
        if not res.success or not url_part:
            raise RuntimeError(
                f'failure to detect URL for RHEL ISO image "{iso0}" at URL "{url}"'
            )
        iso1 = f"{url}{url_part}"
    else:
        # Not a "rhel:" URL. This is either a HTTP/HTTPS URL or a pathname.
        # Pass on to iso1.
        iso1 = iso0

    # Is iso1 a HTTP/HTTPS URL? In that case, download the file to disk.
    # Assign the result in path to "iso2".
    if iso1.startswith("http://") or iso1.startswith("https://"):
        import hashlib

        filename = iso1[(iso1.rfind("/") + 1) :]

        filename_name, filename_ext = os.path.splitext(filename)
        filename_digest = hashlib.sha256(iso1.encode()).hexdigest()[:8]

        filename = f"{filename_name}.{filename_digest}{filename_ext}"

        iso2 = os.path.join(chroot_path, f"root/rhel-iso-{filename}")
        if force or not os.path.exists(iso2):
            iso2_tmp = f"{iso2}.tmp"
            ret = host.local.run(
                f"curl -L -k -o {shlex.quote(iso2_tmp)} {shlex.quote(iso1)} && mv {shlex.quote(iso2_tmp)} {shlex.quote(iso2)}",
                log_level_fail=logging.ERROR,
            )
            if not ret.success:
                raise RuntimeError(
                    f'failure to download RHEL ISO image "{iso1}" to "{iso2}"'
                )
        else:
            cached_http_file = True
            iso_url = iso1
    else:
        # Not a HTTP/HTTPS URL. This is expected to be a pathname already.
        # Pass on to iso2.
        iso2 = iso1

    # At this point, "iso2" is expected to hold the filename for the image.
    if not os.path.exists(iso2):
        raise RuntimeError(f'iso path "{iso}" ("{iso2}") does not exist')

    logger.info(f"use iso {shlex.quote(iso2)}")
    return iso2, iso_url, cached_http_file


def ignition_storage_file(
    *,
    path: str,
    contents: str,
    mode: int = 0o644,
    user: str = "root",
    group: str = "root",
    overwrite: bool = True,
    encode: typing.Literal["plain", "base64"] = "base64",
) -> dict[str, typing.Any]:
    if encode == "plain":
        ct = f"data:,{contents}"
    elif encode == "base64":
        ct = common.base64_encode(
            contents,
            prefix="data:;base64,",
        )
    else:
        raise ValueError("encode")
    return {
        "path": path,
        "mode": mode,
        "user": {"name": user},
        "group": {"name": group},
        "overwrite": overwrite,
        "contents": {"source": ct},
    }


def run_main(
    main_fcn: Callable[[], None],
    *,
    extra_cleanup: Optional[Callable[[], None]] = None,
) -> None:
    def _cleanup() -> None:
        common.thread_list_join_all()
        if extra_cleanup is not None:
            extra_cleanup()

    common.run_main(main_fcn, cleanup=_cleanup)
