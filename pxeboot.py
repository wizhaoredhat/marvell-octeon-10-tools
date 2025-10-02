#!/usr/bin/env python3

import abc
import argparse
import collections.abc
import dataclasses
import datetime
import itertools
import logging
import os
import shlex
import shutil
import sys
import time
import typing

from typing import Optional

from ktoolbox import common
from ktoolbox import host
from ktoolbox import netdev

import common_dpu

from common_dpu import ESC
from common_dpu import KEY_DOWN
from common_dpu import KEY_ENTER
from common_dpu import logger
from reset import reset


TFTP_PATH = "/var/lib/tftpboot"
MNT_PATH = "/mnt/marvell_dpu_iso"
WWW_PATH = "/www"


@dataclasses.dataclass(frozen=True, **common.KW_ONLY_DATACLASS)
class Config:
    dpu_name: str = "marvell-dpu"
    iso: str = "rhel:"
    host_path: str = "/host"
    cfg_host_mode: str = "auto"
    dev: str = "eno4"
    cfg_ssh_keys: Optional[tuple[str, ...]] = None
    host_setup_only: bool = False
    yum_repos: str = "none"
    octep_cp_agent_service_enable: bool = True
    nm_secondary_cloned_mac_address: str = ""
    nm_secondary_ip_address: str = ""
    nm_secondary_ip_gateway: str = ""
    extra_packages: tuple[str, ...] = ()
    default_extra_packages: bool = False
    console_wait: float = 60.0
    prompt: bool = False

    def __post_init__(self) -> None:
        if self.yum_repos not in ("none", "rhel-nightly"):
            raise ValueError("yum_repos")
        if self.cfg_host_mode not in ("auto", "rhel", "coreos", "ephemeral"):
            raise ValueError("hostmode")


@dataclasses.dataclass(frozen=True, **common.KW_ONLY_DATACLASS)
class RunContext(common.ImmutableDataclass):
    cfg: Config

    def host_mode_set_once(self) -> None:
        host_mode = self.cfg.cfg_host_mode
        if host_mode == "auto":
            host_mode = detect_host_mode(host_path=self.cfg.host_path)
        self._field_set_once("host_mode", host_mode)

    @property
    def host_mode(self) -> str:
        return self._field_get("host_mode", str)

    def ssh_keys_set_once(self, ssh_keys: collections.abc.Iterable[str]) -> None:
        self._field_set_once("ssh_keys", tuple(ssh_keys))

    @property
    def ssh_keys(self) -> tuple[str, ...]:
        return self._field_get("ssh_keys", tuple)

    def iso_kind_set_once(self, iso_kind: "IsoKind") -> None:
        self._field_set_once("iso_kind", iso_kind)

    @property
    def iso_kind(self) -> "IsoKind":
        val: IsoKind = self._field_get("iso_kind")
        return val


@dataclasses.dataclass(frozen=True)
class IsoKind(abc.ABC):
    CHECK_FILES: typing.ClassVar[tuple[str, ...]]

    @staticmethod
    def detect_from_iso(
        *,
        read_check: bool = False,
    ) -> Optional["IsoKind"]:
        for iso_kind_type in (IsoKindRhel,):
            iso_kind = iso_kind_type()
            if common_dpu.check_files(
                iso_kind.CHECK_FILES,
                cwd=MNT_PATH,
                read_check=read_check,
            ):
                return iso_kind
        return None


class IsoKindRhel(IsoKind):
    CHECK_FILES = (
        "EFI/BOOT/grubaa64.efi",
        "images/pxeboot/initrd.img",
        "images/pxeboot/vmlinuz",
        "media.repo",
    )

    @staticmethod
    def copy_kickstart(
        host_path: str,
        dpu_name: str,
        ssh_keys: tuple[str, ...],
        yum_repos: str,
        octep_cp_agent_service_enable: bool,
        nm_secondary_cloned_mac_address: str,
        nm_secondary_ip_address: str,
        nm_secondary_ip_gateway: str,
        extra_packages: tuple[str, ...],
        default_extra_packages: bool,
    ) -> None:
        ip_address = ""
        if nm_secondary_ip_address:
            ip_address = f"address1={nm_secondary_ip_address}"
            if nm_secondary_ip_gateway:
                ip_address += f",{nm_secondary_ip_gateway}"

        with open(common_dpu.packaged_file("manifests/pxeboot/kickstart.ks"), "r") as f:
            kickstart = f.read()

        yum_repo_enabled = yum_repos == "rhel-nightly"

        kickstart = kickstart.replace("@__HOSTNAME__@", shlex.quote(dpu_name))
        kickstart = kickstart.replace(
            "@__SSH_PUBKEY__@", shlex.quote("\n".join(ssh_keys))
        )
        kickstart = kickstart.replace("@__DPU_IP4ADDRNET__@", common_dpu.dpu_ip4addrnet)
        kickstart = kickstart.replace("@__HOST_IP4ADDR__@", common_dpu.host_ip4addr)
        kickstart = kickstart.replace(
            "@__NM_SECONDARY_CLONED_MAC_ADDRESS__@",
            nm_secondary_cloned_mac_address,
        )
        kickstart = kickstart.replace(
            "@__NM_SECONDARY_IP_ADDRESS__@",
            ip_address,
        )
        kickstart = kickstart.replace(
            "@__YUM_REPO_URL__@", shlex.quote(detect_yum_repo_url())
        )
        kickstart = kickstart.replace(
            "@__YUM_REPO_ENABLED__@",
            common.bool_to_str(yum_repo_enabled, format="1"),
        )
        kickstart = kickstart.replace(
            "@__EXTRA_PACKAGES__@",
            " ".join(shlex.quote(s) for s in extra_packages),
        )
        kickstart = kickstart.replace(
            "@__DEFAULT_EXTRA_PACKAGES__@",
            "1" if default_extra_packages else "0",
        )
        kickstart = kickstart.replace(
            "@__OCTEP_CP_AGENT_SERVICE_ENABLE__@",
            common.bool_to_str(octep_cp_agent_service_enable, format="1"),
        )

        res = host.local.run(
            [
                "grep",
                "-R",
                "-h",
                "^ *server ",
                f"{host_path}/run/chrony-dhcp/",
                f"{host_path}/etc/chrony.conf",
            ]
        )
        kickstart = kickstart.replace("@__CHRONY_SERVERS__@", res.out)

        for ks_lines in kickstart.splitlines(keepends=True):
            logger.info(f"kickstart: {repr(ks_lines)}")

        with open(f"{WWW_PATH}/kickstart.ks", "w") as f:
            f.write(kickstart)


def parse_args() -> RunContext:
    parser = argparse.ArgumentParser(description="Process ISO file.")
    parser.add_argument(
        "iso",
        type=str,
        nargs="?",
        default=Config.iso,
        help=f'Select the RHEL ISO to install. This can be a file name (make sure to map the host with `-v /:/host` and specify the path name starting with "/host"); it can be a HTTP URL (in which case the file will be downloaded to /host/root/rhel-iso-$NAME if such file does not exist yet); it can also be "rhel:9.x" which will automatically detect the right HTTP URL to download the latest iso. Default: "rhel:" to choose RHEL version {common_dpu.DEFAULT_RHEL_ISO}.',
    )
    parser.add_argument(
        "--dev",
        type=str,
        default=Config.dev,
        help="Optional argument of type string for device. Default is 'eno4'.",
    )
    parser.add_argument(
        "--host-path",
        type=str,
        default=Config.host_path,
        help="Optional argument where the host filesystem is mounted. Default is '/host'. Run podman with \"-v /:/host\".",
    )
    parser.add_argument(
        "--ssh-key",
        action="append",
        help='Specify SSH public keys to add to the DPU\'s /root/.ssh/authorized_keys. Can be specified multiple times. If unspecified or set to "", include "/{host-path}/root/.ssh/id_ed25519.pub" (this file will be generated with "--host-mode=rhel" if it doesn\'t exist).',
    )
    parser.add_argument(
        "--yum-repos",
        choices=["none", "rhel-nightly"],
        default=Config.yum_repos,
        help='We generate "/etc/yum.repos.d/marvell-tools-beaker.repo" with latest RHEL9 nightly compose. However, that repo is disabled unless "--yum-repos=rhel-nightly".',
    )
    parser.add_argument(
        "--host-mode",
        choices=["auto", "rhel", "coreos", "ephemeral"],
        default=Config.cfg_host_mode,
        help='How to treat the host. With "rhel" and "coreos" we configure a (persisted) NetworkManager connection profile for device (eno4). With "ephemeral"", this only configures an ad-hoc IP address with iproute. Port forwarding is always ephemeral via nft rules.',
    )
    parser.add_argument(
        "-H",
        "--host-setup-only",
        action="store_true",
        help="Installing the DPU also creates some ephemeral configuration. If you reboot the host, this is lost. Run the command with --host-setup-only to only recreate this configuration. This is idempotent.",
    )
    parser.add_argument(
        "--dpu-name",
        type=str,
        default=Config.dpu_name,
        help='The static hostname of the DPU. Defaults to "marvell-dpu". With "--host-mode" set to "rhel" or "coreos", this is also added to /etc/hosts alongside "dpu".',
    )
    parser.add_argument(
        "-W",
        "--console-wait",
        type=float,
        default=Config.console_wait,
        help='After installation is started, the tool will stay connected to the serial port for the specified amount of time. The benefit is that we see what happens in the output of the tool. The downside is that we cannot attach a second terminal to the serial port during that time. Defaults to 60 seconds. The console output is also written to "{host-path}/tmp/pxeboot-serial.*.log".',
    )
    parser.add_argument(
        "--nm-secondary-cloned-mac-address",
        type=str,
        default=Config.nm_secondary_cloned_mac_address,
        help='The MAC address to configure on the "enP2p2s0-dpu-secondary" profile.',
    )
    parser.add_argument(
        "--nm-secondary-ip-address",
        type=str,
        default=Config.nm_secondary_ip_address,
        help='If set, configure a static ipv4.addresses on the profile "enP2p2s0-dpu-secondary". This should contain the subnet, for example "192.168.122.5/24".',
    )
    parser.add_argument(
        "--nm-secondary-ip-gateway",
        type=str,
        default=Config.nm_secondary_ip_gateway,
        help='If set, configure ipv4.gateway on the "enP2p2s0-dpu-secondary" (requires "--nm-secondary-ip-address"). This should be in the same subnet as the address.',
    )
    parser.add_argument(
        "-P",
        "--prompt",
        action="store_true",
        help="If set, start DHCP/TFTP/HTTP services and wait for the user to press ENTER. This can be used to manually boot via PXE.",
    )
    parser.add_argument(
        "--octep-cp-agent-service-enable",
        action="store_true",
        default=Config.octep_cp_agent_service_enable,
        help='The opposite of "--octep-cp-agent-service-disable".',
    )
    parser.add_argument(
        "--octep-cp-agent-service-disable",
        action="store_false",
        dest="octep_cp_agent_service_enable",
        help='The tool will always create a "octep_cp_agent.service". By default this service is enabled and running. Use this flag to disable the service.',
    )
    parser.add_argument(
        "-i",
        "--extra-package",
        action="append",
        help="List of extra packages that are installed during kickstart.",
    )
    parser.add_argument(
        "--default-extra-packages",
        action="store_true",
        help="If true, install additional default packages during kickstart. See '@__DEFAULT_EXTRA_PACKAGES__@' in \"manifests/pxeboot/kickstart.ks\".",
    )

    args = parser.parse_args()

    cfg = Config(
        dpu_name=args.dpu_name,
        iso=args.iso,
        host_path=args.host_path,
        cfg_host_mode=args.host_mode,
        dev=args.dev,
        cfg_ssh_keys=None if args.ssh_key is None else tuple(args.ssh_key),
        host_setup_only=args.host_setup_only,
        yum_repos=args.yum_repos,
        octep_cp_agent_service_enable=args.octep_cp_agent_service_enable,
        nm_secondary_cloned_mac_address=args.nm_secondary_cloned_mac_address,
        nm_secondary_ip_address=args.nm_secondary_ip_address,
        nm_secondary_ip_gateway=args.nm_secondary_ip_gateway,
        extra_packages=tuple(args.extra_package or ()),
        default_extra_packages=args.default_extra_packages,
        console_wait=args.console_wait,
        prompt=args.prompt,
    )

    ctx = RunContext(cfg=cfg)

    return ctx


def detect_host_mode(*, host_path: str) -> str:
    if host.local.run(
        [
            "grep",
            "-q",
            'NAME="Red Hat Enterprise Linux"',
            f"{host_path}/etc/os-release",
        ],
        log_level_result=logging.INFO,
    ):
        return "rhel"
    return "coreos"


def wait_for_boot(ctx: RunContext, ser: common.Serial) -> None:
    has_ser = True
    time_start = time.monotonic()
    timeout = max(ctx.cfg.console_wait + 100.0, 1800.0)
    logger.info(f"Wait for boot and IP address {common_dpu.dpu_ip4addr}")
    sleep_time = 60
    while True:

        if has_ser and time.monotonic() > time_start + ctx.cfg.console_wait:
            logger.info(f"Closing serial console {ser.port}")
            has_ser = False

        # We rely on configuring a static IP address on the installed host.
        #
        # For one, to always have that IP address there (even after there
        # is no more DHCP server running) is useful to access the host.
        #
        # But also, if we would wait here to ping one of the DHCP addresses,
        # then we wouldn't easily know whether the installer is still running
        # or installation completed with successful. To find the static IP
        # address quite reliably tells us that the host is up.
        if netdev.wait_ping(common_dpu.dpu_ip4addr) is not None:
            logger.info(f"got response from {common_dpu.dpu_ip4addr}")
            break

        if time.monotonic() > time_start + timeout:
            raise RuntimeError(
                f"Failed to detect IP {common_dpu.dpu_ip4addr} on Marvell card"
            )

        if has_ser:
            # Read and log the output for a bit longer. This way, we see how the
            # DPU starts installation.
            ser.expect(pattern=None, timeout=sleep_time)
        else:
            time.sleep(sleep_time)

        sleep_time = max(int(sleep_time / 1.3), 9)


def create_serial(ctx: RunContext) -> common.Serial:
    # We also write the data from the serial port to "{host_path}/tmp/pxeboot-serial-*.log"
    # on the host. For debugging, you can find what was written there.
    log_stream_filename = f"{ctx.cfg.host_path}/tmp/pxeboot-serial.{datetime.datetime.now():%Y%m%d-%H%M%S.%f}.log"

    logger.info(
        f"Select entry and boot in {common_dpu.TTYUSB0} (log to {log_stream_filename})"
    )

    log_stream = open(log_stream_filename, "ab", buffering=0)

    return common.Serial(
        common_dpu.TTYUSB0,
        log_stream=log_stream,
        own_log_stream=True,
    )


def select_pxe_entry(ctx: RunContext, ser: common.Serial) -> None:
    logger.info("waiting for instructions to access boot menu")
    ser.expect("Press 'B' within 10 seconds for boot menu", 30)
    time.sleep(1)
    logger.info("Pressing B to access boot menu")
    ser.send("b")
    logger.info("waiting for instructions to Boot from Secondary Boot Device")
    ser.expect("2\\) Boot from Secondary Boot Device", 10)
    time.sleep(1)
    ser.send("2")
    logger.info("waiting to escape to UEFI boot menu")
    ser.expect("Press ESCAPE for boot options", 60)
    logger.info("Sending escape 5 times")
    ser.send(ESC * 5)
    logger.info("waiting on language option")
    ser.expect(
        "This is the option.*one adjusts to change.*the language for the.*current system",
        3,
    )
    logger.info("pressing down")
    ser.send(KEY_DOWN)
    time.sleep(1)
    logger.info("pressing down again")
    ser.send(KEY_DOWN)
    logger.info("waiting for Boot manager entry")
    ser.expect("This selection will.*take you to the Boot.*Manager", 3)
    ser.send(KEY_ENTER)
    ser.expect("Device Path")
    retry = 30
    logger.info(f"Trying up to {retry} times to find pxe boot interface")
    while retry:
        ser.send(KEY_DOWN)
        try:
            # TODO: FIXME: We need to read the port configuration.
            # e.g. 80AA99887766 + number of lanes used in the SERDES
            ser.expect("UEFI PXEv4.*MAC:80AA99887767", 0.5)
            break
        except Exception:
            retry -= 1
    if not retry:
        e = Exception("Didn't find boot interface")
        logger.info(e)
        raise e
    else:
        logger.info(f"Found boot interface after {30 - retry} tries, sending enter")
        ser.send(KEY_ENTER)
        time.sleep(10)
        # Use the ^ and v keys to select which entry is highlighted.
        # Press enter to boot the selected OS, `e' to edit the commands
        # before booting or `c' for a command-line.
        # time.sleep(1)
        # timeout = 30


def write_hosts_entry(ctx: RunContext) -> None:
    common.etc_hosts_update_file(
        {
            ctx.cfg.dpu_name: (common_dpu.dpu_ip4addr, ["dpu"]),
        },
        f"{ctx.cfg.host_path}/etc/hosts",
    )


def post_pxeboot(ctx: RunContext) -> None:
    if ctx.host_mode in ("rhel", "coreos"):
        write_hosts_entry(ctx)


def detect_yum_repo_url() -> str:
    res = host.local.run(
        [
            "sed",
            "-n",
            "s/^name=Red Hat Enterprise Linux \\([0-9]\\+\\.[0-9]\\+\\).0$/\\1/p",
            f"{MNT_PATH}/media.repo",
        ]
    )
    if res.success and res.out:
        os_version = res.out.splitlines()[-1].strip()
        url_base = (
            "http://download.hosts.prod.upshift.rdu2.redhat.com/rhel-9/composes/RHEL-9/"
        )
        sed_pattern = f's/.*href="\\(RHEL-{os_version}.0-updates[^"]*\\)".*/\\1/p'
        res = host.local.run(
            f"curl -L -s {shlex.quote(url_base)} | "
            f"sed -n {shlex.quote(sed_pattern)} | "
            "grep -v delete-me/ | sort | tail -n1"
        )
        if res.success:
            part = res.out.strip()
            if part:
                return f"{url_base}{part}"
    return ""


def setup_http(ctx: RunContext) -> None:
    os.makedirs(WWW_PATH, exist_ok=True)
    host.local.run(["ln", "-snf", MNT_PATH, f"{WWW_PATH}/marvell_dpu_iso"])

    IsoKindRhel.copy_kickstart(
        ctx.cfg.host_path,
        ctx.cfg.dpu_name,
        ctx.ssh_keys,
        ctx.cfg.yum_repos,
        ctx.cfg.octep_cp_agent_service_enable,
        ctx.cfg.nm_secondary_cloned_mac_address,
        ctx.cfg.nm_secondary_ip_address,
        ctx.cfg.nm_secondary_ip_gateway,
        ctx.cfg.extra_packages,
        ctx.cfg.default_extra_packages,
    )

    common_dpu.run_process(
        "httpd",
        [
            sys.executable,
            "-m",
            "http.server",
            "-d",
            WWW_PATH,
            "24380",
        ],
    )


def setup_tftp(ctx: RunContext) -> None:
    logger.info("Configuring TFTP")
    os.makedirs(f"{TFTP_PATH}/pxelinux", exist_ok=True)
    logger.info("starting in.tftpd")
    host.local.run("killall in.tftpd")
    common_dpu.run_process("tftp", "/usr/sbin/in.tftpd -s -B 1468 -L /var/lib/tftpboot")
    shutil.copy(f"{MNT_PATH}/images/pxeboot/vmlinuz", f"{TFTP_PATH}/pxelinux")
    shutil.copy(f"{MNT_PATH}/images/pxeboot/initrd.img", f"{TFTP_PATH}/pxelinux")
    shutil.copy(f"{MNT_PATH}/EFI/BOOT/grubaa64.efi", f"{TFTP_PATH}/")
    os.chmod(f"{TFTP_PATH}/grubaa64.efi", 0o744)
    shutil.copy(
        common_dpu.packaged_file("manifests/pxeboot/grub.cfg"),
        f"{TFTP_PATH}/grub.cfg",
    )


def prepare_host(ctx: RunContext) -> list[str]:
    if ctx.host_mode in ("rhel", "coreos"):
        common_dpu.nmcli_setup_mngtiface(
            ifname=ctx.cfg.dev,
            chroot_path=ctx.cfg.host_path,
            ip4addr=common_dpu.host_ip4addrnet,
        )
    else:
        host.local.run(
            f"ip addr add {shlex.quote(common_dpu.host_ip4addrnet)} dev {shlex.quote(ctx.cfg.dev)}"
        )

    common_dpu.nft_masquerade(ifname=ctx.cfg.dev, subnet=common_dpu.dpu_subnet)
    host.local.run("sysctl -w net.ipv4.ip_forward=1")

    ssh_keys = []

    add_host_key = True
    if ctx.cfg.cfg_ssh_keys:
        add_host_key = False
        for s in ctx.cfg.cfg_ssh_keys:
            if not s:
                add_host_key = True
            else:
                ssh_keys.append(s)

    if add_host_key:
        ssh_privkey_file = common_dpu.ssh_generate_key(
            ctx.cfg.host_path,
            create=True,
        )
        if ssh_privkey_file is not None:
            logger.info(f"prepare-host: add host key {repr(ssh_privkey_file)}")
            ssh_keys.append(common_dpu.ssh_read_pubkey(ssh_privkey_file))

    if not ssh_keys:
        logger.info("prepare-host: no SSH keys")
    else:
        for k in ssh_keys:
            logger.info(f"prepare-host: use SSH key {repr(k)}")

    return ssh_keys


def setup_dhcp(ctx: RunContext) -> None:
    common_dpu.run_dhcpd()


def create_and_mount_iso(ctx: RunContext) -> IsoKind:
    is_retry = False
    iso2 = ctx.cfg.iso
    while True:

        # We set `force=is_retry`. On retry (a second run of the loop) we will
        # force a re-download of the file. Contrary to the first run, where we
        # might accept an existing file on disk.
        iso_path, iso_url, cached_http_file = common_dpu.create_iso_file(
            iso2,
            chroot_path=ctx.cfg.host_path,
            force=is_retry,
        )

        success = common_dpu.mount_iso(
            iso_path,
            mount_path=MNT_PATH,
        )
        if success:
            iso_kind = IsoKind.detect_from_iso(read_check=True)
            if iso_kind is not None:
                logger.info(
                    f"ISO {iso_path} successfully mounted at {MNT_PATH} (as {iso_kind})"
                )
                return iso_kind
            host.local.run(["umount", MNT_PATH])

        if is_retry or not cached_http_file:
            # On first try, if the ISO was found on disk (and the path was a
            # HTTP URL), we accept that the file might be broken. We will retry
            # in that case.
            #
            # But on retry, or if this was not a HTTP URL, this is a fatal
            # error.
            logger.error(f"Failure to mount ISO {ctx.cfg.iso!r}")
            raise RuntimeError(f"Failure to mount ISO {ctx.cfg.iso!r}")

        iso2 = common.unwrap(iso_url)
        is_retry = True
        logger.warning(f"ISO {iso_path} seems broken. Try re-downloading {iso2}")


def dpu_pxeboot(ctx: RunContext) -> None:
    logger.info("Resetting card")
    reset()
    with create_serial(ctx) as ser:
        select_pxe_entry(ctx, ser)
        wait_for_boot(ctx, ser)


def main() -> None:
    logger.info(f"pxeboot: {shlex.join(shlex.quote(s) for s in sys.argv)}")
    ctx = parse_args()
    logger.info(f"pxeboot run context: {ctx}")

    ctx.host_mode_set_once()

    logger.info("Preparing services for Pxeboot")
    ssh_keys = prepare_host(ctx)

    ctx.ssh_keys_set_once(ssh_keys)

    if not ctx.cfg.host_setup_only:
        iso_kind = create_and_mount_iso(ctx)
        ctx.iso_kind_set_once(iso_kind)
        setup_dhcp(ctx)
        setup_tftp(ctx)
        setup_http(ctx)
        logger.info("Giving services time to settle")
        time.sleep(3)

        common_dpu.check_services_running()

        if ctx.cfg.prompt:
            input(
                "dhcp/tftp/http services started. Waiting. Press ENTER to continue or abort with CTRL+C"
            )

        for try_count in itertools.count(start=1):
            logger.info(f"Starting UEFI PXE Boot (try {try_count})")
            try:
                dpu_pxeboot(ctx)
            except Exception as e:
                if try_count >= 3:
                    raise RuntimeError(f"Failure to pxeboot: {e}") from e
                logger.warning(f"Failure to pxeboot (try {try_count}): {e}")
                continue
            break

    post_pxeboot(ctx)

    logger.info("Terminating http, tftp, and dhcpd")
    common.thread_list_join_all()

    if ctx.cfg.host_setup_only:
        host_setup_only_msg = " (host-setup-only)"
    else:
        host_setup_only_msg = ""

    logger.info(
        f"SUCCESS{host_setup_only_msg}. Try `ssh root@{common_dpu.dpu_ip4addr}`"
    )


if __name__ == "__main__":
    common_dpu.run_main(main)
