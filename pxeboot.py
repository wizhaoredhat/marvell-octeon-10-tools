#!/usr/bin/env python3

import abc
import argparse
import collections.abc
import dataclasses
import datetime
import enum
import itertools
import json
import logging
import os
import re
import shlex
import shutil
import signal
import sys
import time
import types
import typing

from typing import Optional

from ktoolbox import common
from ktoolbox import host
from ktoolbox import netdev

import common_dpu

from common_dpu import ESC
from common_dpu import KEY_UP
from common_dpu import KEY_DOWN
from common_dpu import KEY_ENTER
from common_dpu import logger
from reset import reset


TFTP_PATH = "/var/lib/tftpboot"
MNT_PATH = "/mnt/marvell_dpu_iso"
WWW_PATH = "/www"


_signal_sigusr1_received = False


def _signal_handler(signum: int, frame: typing.Any) -> None:
    global _signal_sigusr1_received
    _signal_sigusr1_received = True


@dataclasses.dataclass(frozen=True, **common.KW_ONLY_DATACLASS)
class Config:
    dpu_name: str = ""
    iso: str = "rhel:"
    cfg_iso_kind: str = "auto"
    host_path: str = "/host"
    cfg_host_mode: str = "auto"
    dev: str = "eno4"
    dpu_dev: str = "primary"
    cfg_ssh_keys: Optional[tuple[str, ...]] = None
    host_setup_only: bool = False
    yum_repos: str = "none"
    octep_cp_agent_service_enable: bool = True
    nm_secondary_cloned_mac_address: str = ""
    nm_secondary_ip_address: str = ""
    nm_secondary_ip_gateway: str = ""
    extra_packages: tuple[str, ...] = ()
    default_extra_packages: bool = False
    console_wait: float = 0.0
    prompt: bool = False
    cfg_dhcp_restricted: str = "auto"

    def __post_init__(self) -> None:
        if self.yum_repos not in ("none", "rhel-nightly"):
            raise ValueError("yum_repos")
        if self.cfg_host_mode not in ("auto", "rhel", "coreos", "ephemeral"):
            raise ValueError("hostmode")
        if self.cfg_dhcp_restricted not in ("auto", "yes", "no"):
            raise ValueError("dhcp_restricted")
        Config.validate_dpu_dev(self.dpu_dev, check_normalized=True)

    @staticmethod
    def validate_dpu_dev(dpu_dev: str, *, check_normalized: bool = False) -> str:
        def _normalize(dpu_dev: str) -> str:
            s1 = dpu_dev.lower().strip()
            if s1 in ("primary", "secondary"):
                return s1
            s2 = netdev.validate_ethaddr_or_none(dpu_dev)
            if s2 is not None:
                return s2
            try:
                val = int(dpu_dev)
            except Exception:
                pass
            else:
                if val >= 0 and val <= 4:
                    return str(val)
            raise ValueError("dpu_dev")

        normalized = _normalize(dpu_dev)

        if normalized == dpu_dev:
            return dpu_dev

        if check_normalized:
            raise ValueError("dpu_dev is not normalized")

        return normalized


@dataclasses.dataclass(frozen=True, **common.KW_ONLY_DATACLASS)
class RunContext(common.ImmutableDataclass):
    cfg: Config

    def _field_notify_set(
        self,
        key: str,
        old_val: typing.Union[common._MISSING_TYPE, typing.Any],
        val: typing.Union[common._MISSING_TYPE, typing.Any],
    ) -> None:
        if isinstance(old_val, common._MISSING_TYPE):
            if isinstance(val, common._MISSING_TYPE):
                pass
            else:
                logger.info(f"context[{key!r}]: initialize to {val!r}")
        else:
            if isinstance(val, common._MISSING_TYPE):
                logger.info(f"context[{key!r}]: unset (was {old_val!r})")
            else:
                logger.info(f"context[{key!r}]: reset to {val!r} (was {old_val!r})")

    def host_mode_set_once(self, host_mode: str) -> None:
        self._field_set_once("host_mode", host_mode)

    @property
    def host_mode(self) -> str:
        return self._field_get("host_mode", str)

    @property
    def host_mode_persist(self) -> bool:
        return self.host_mode in ("rhel", "coreos")

    def ssh_keys_set_once(self, ssh_keys: collections.abc.Iterable[str]) -> None:
        self._field_set_once("ssh_keys", tuple(ssh_keys))

    @property
    def ssh_keys(self) -> tuple[str, ...]:
        return self._field_get("ssh_keys", tuple)

    def ssh_privkey_file_set_once(self, ssh_privkey_file: str) -> None:
        self._field_set_once("ssh_privkey_file", [ssh_privkey_file, True])

    @property
    def ssh_privkey_file(self) -> str:
        ssh_privkey_file: str
        has: bool
        val = self._field_get("ssh_privkey_file", list)
        with self._lock:
            ssh_privkey_file, has = val
        if not has:
            raise RuntimeError("The private key was already deleted")
        return ssh_privkey_file

    def ssh_privkey_file_cleanup(self) -> None:
        ssh_privkey_file: str
        has: bool
        val = self._field_get(
            "ssh_privkey_file",
            list,
            on_missing=lambda: ["", False],
        )
        with self._lock:
            ssh_privkey_file, has = val
            if not has:
                return
            val[1] = False
        try:
            os.remove(ssh_privkey_file)
        except Exception:
            pass

    def iso_kind_set_once(self, iso_kind: "IsoKind") -> None:
        self._field_set_once("iso_kind", iso_kind)

    @property
    def iso_kind(self) -> "IsoKind":
        val: IsoKind = self._field_get("iso_kind")
        return val

    @property
    def dpu_name(self) -> Optional[str]:
        if self.cfg.dpu_name:
            return self.cfg.dpu_name
        if isinstance(self.iso_kind, IsoKindRhel):
            return "marvell-dpu"
        return None

    def dpu_mac_ensure(
        self,
        *,
        reuse_serial_context: bool = False,
    ) -> tuple[str, bool]:
        in_boot_menu = False

        def _on_missing() -> str:
            nonlocal in_boot_menu

            dpu_mac2, in_boot_menu = detect_dpu_mac(
                self,
                reuse_serial_context=reuse_serial_context,
            )
            return dpu_mac2

        dpu_mac = self._field_get(
            "dpu_mac",
            str,
            on_missing=_on_missing,
        )
        return dpu_mac, in_boot_menu

    def dpu_macs_ensure(self) -> tuple[dict[int, str], bool]:
        dpu_macs, in_boot_menu = self._field_get_or_create(
            "dpu_macs",
            dict,
            on_missing=lambda: uefi_enter_boot_menu_and_detect_dpu_macs(self),
        )
        return (dpu_macs, in_boot_menu)

    def dhcp_restricted_ensure(self) -> bool:
        return self._field_init_once(
            "dhcp_restricted",
            lambda: detect_dhcp_restricted(self),
            valtype=bool,
        )

    def serial_create(self) -> common.Serial:

        ser, was_created = self._field_get_or_create(
            "serial",
            common.Serial,
            on_missing=lambda: create_serial(host_path=self.cfg.host_path),
        )

        if not was_created:
            raise RuntimeError("A serial already exists. Cannot create another one")

        logger.info(f"serial[{ser.port}]: creating serial connection")
        return ser

    def serial_close(self) -> None:
        ser, had = self._field_set(
            "serial",
            common.MISSING,
            valtype=common.Serial,
            allow_exists=True,
        )
        logger.info(f"serial[{ser.port}]: closing serial connection")
        ser.close()

    def serial_get(self) -> common.Serial:
        ser, has = self._field_check("serial", common.Serial)
        if not has:
            raise RuntimeError("Cannot access serial without creating it first")
        return common.unwrap(ser)

    def serial_open(self) -> typing.ContextManager[common.Serial]:
        ctx = self

        class SerialContext(typing.ContextManager[common.Serial]):
            def __enter__(self) -> common.Serial:
                return ctx.serial_create()

            def __exit__(
                self,
                exc_type: Optional[type[BaseException]],
                exc_value: Optional[BaseException],
                traceback: Optional[types.TracebackType],
            ) -> Optional[bool]:
                ctx.serial_close()
                return None

        return SerialContext()

    def before_prompt_set_after(self) -> None:
        self._field_set_once("before_prompt", True)

    @property
    def before_prompt(self) -> bool:
        if not self.cfg.prompt:
            return False
        val, has = self._field_check("before_prompt", bool)
        return not has


def nm_profile_nm_host() -> str:
    return f"""[connection]
id=enP2p3s0-dpu-host
uuid={common.uuid4()}
type=ethernet
autoconnect-priority=10
interface-name=enP2p3s0

[ipv4]
method=auto
address1={common_dpu.dpu_ip4addrnet},{common_dpu.host_ip4addr}
dhcp-timeout=2147483647
route-metric=120

[ipv6]
method=auto
addr-gen-mode=eui64
route-metric=120
"""


def nm_profile_nm_secondary(ctx: RunContext) -> str:
    ip_address = ""
    if ctx.cfg.nm_secondary_ip_address:
        ip_address = f"address1={ctx.cfg.nm_secondary_ip_address}"
        if ctx.cfg.nm_secondary_ip_gateway:
            ip_address += f",{ctx.cfg.nm_secondary_ip_gateway}"

    return f"""[connection]
id=enP2p2s0-dpu-secondary
uuid={common.uuid4()}
type=ethernet
autoconnect-priority=20
interface-name=enP2p2s0

[ethernet]
cloned-mac-address={ctx.cfg.nm_secondary_cloned_mac_address}

[ipv4]
method=auto
dhcp-timeout=2147483647
route-metric=110
{ip_address}

[ipv6]
method=auto
addr-gen-mode=eui64
route-metric=110
"""


def nm_conf_unmanaged_devices() -> str:
    return """# Generated by marvell-tools' pxeboot command to mark the PF/VFs as unmanaged.
[device-89-marvell-tools-unmanage-devices]
match-device=interface-name:enP2p1s0*
managed=0
"""


@dataclasses.dataclass(frozen=True)
class IsoKind(abc.ABC):
    NAME: typing.ClassVar[str]
    CHECK_FILES: typing.ClassVar[tuple[str, ...]]
    DHCP_PXE_FILENAME: typing.ClassVar[str]
    SSH_USER: typing.ClassVar[str] = "root"

    @staticmethod
    def detect_from_iso(
        *,
        cfg_iso_kind: Optional[str] = None,
        check_mount: bool = True,
        read_check: bool = False,
    ) -> Optional["IsoKind"]:
        if cfg_iso_kind:
            cfg_iso_kind = cfg_iso_kind.strip().lower()

        is_auto = not cfg_iso_kind or cfg_iso_kind == "auto"

        for iso_kind_type in (IsoKindRhel, IsoKindRhcos):
            iso_kind = iso_kind_type()

            if not is_auto and iso_kind.NAME != cfg_iso_kind:
                continue

            if check_mount:
                if not common_dpu.check_files(
                    iso_kind.CHECK_FILES,
                    cwd=MNT_PATH,
                    read_check=read_check,
                ):
                    continue
            else:
                if is_auto:
                    continue

            return iso_kind

        return None

    def __str__(self) -> str:
        return self.NAME

    @abc.abstractmethod
    def setup_tftp_files(self) -> None:
        pass

    def mount_nested_iso(self) -> None:
        pass

    @abc.abstractmethod
    def setup_http_files(self, ctx: RunContext) -> None:
        pass


class IsoKindRhel(IsoKind):
    NAME = "rhel"
    CHECK_FILES = (
        "EFI/BOOT/grubaa64.efi",
        "images/pxeboot/initrd.img",
        "images/pxeboot/vmlinuz",
        "media.repo",
    )
    DHCP_PXE_FILENAME = "/grubaa64.efi"

    def setup_tftp_files(self) -> None:
        shutil.copy(f"{MNT_PATH}/images/pxeboot/vmlinuz", f"{TFTP_PATH}/pxelinux")
        shutil.copy(f"{MNT_PATH}/images/pxeboot/initrd.img", f"{TFTP_PATH}/pxelinux")
        shutil.copy(f"{MNT_PATH}/EFI/BOOT/grubaa64.efi", f"{TFTP_PATH}/")
        os.chmod(f"{TFTP_PATH}/grubaa64.efi", 0o744)
        shutil.copy(
            common_dpu.packaged_file("manifests/pxeboot/grub.cfg.rhel"),
            f"{TFTP_PATH}/grub.cfg",
        )

    def setup_http_files(self, ctx: RunContext) -> None:
        with open(common_dpu.packaged_file("manifests/pxeboot/kickstart.ks"), "r") as f:
            kickstart = f.read()

        yum_repo_enabled = ctx.cfg.yum_repos == "rhel-nightly"

        kickstart = kickstart.replace("@__HOSTNAME__@", shlex.quote(ctx.dpu_name or ""))
        kickstart = kickstart.replace(
            "@__SSH_PUBKEY__@", shlex.quote("\n".join(ctx.ssh_keys))
        )
        kickstart = kickstart.replace(
            "@__NM_PROFILE_NM_SECONDARY__@",
            nm_profile_nm_secondary(ctx),
        )
        kickstart = kickstart.replace(
            "@__NM_PROFILE_NM_HOST__@",
            nm_profile_nm_host(),
        )
        kickstart = kickstart.replace(
            "@__NM_CONF_UNMANAGED_DEVICES__@",
            nm_conf_unmanaged_devices(),
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
            " ".join(shlex.quote(s) for s in ctx.cfg.extra_packages),
        )
        kickstart = kickstart.replace(
            "@__DEFAULT_EXTRA_PACKAGES__@",
            common.bool_to_str(ctx.cfg.default_extra_packages, format="1"),
        )
        kickstart = kickstart.replace(
            "@__OCTEP_CP_AGENT_SERVICE_ENABLE__@",
            common.bool_to_str(ctx.cfg.octep_cp_agent_service_enable, format="1"),
        )

        res = host.local.run(
            [
                "grep",
                "-R",
                "-h",
                "^ *server ",
                f"{ctx.cfg.host_path}/run/chrony-dhcp/",
                f"{ctx.cfg.host_path}/etc/chrony.conf",
            ]
        )
        kickstart = kickstart.replace("@__CHRONY_SERVERS__@", res.out)

        for ks_lines in kickstart.splitlines(keepends=True):
            logger.info(f"kickstart: {repr(ks_lines)}")

        with open(f"{WWW_PATH}/kickstart.ks", "w") as f:
            f.write(kickstart)


class IsoKindRhcos(IsoKind):
    NAME = "rhcos"
    CHECK_FILES = (
        "images/efiboot.img",
        "images/ignition.img",
        "images/pxeboot/initrd.img",
        "images/pxeboot/rootfs.img",
        "images/pxeboot/vmlinuz",
    )
    DHCP_PXE_FILENAME = "/BOOTAA64.EFI"
    SSH_USER = "core"

    MNT_EFIBOOT_PATH = "/mnt/efiboot"

    def mount_nested_iso(self) -> None:
        success = common_dpu.mount_iso(
            f"{MNT_PATH}/images/efiboot.img",
            mount_path=IsoKindRhcos.MNT_EFIBOOT_PATH,
        )
        if not success:
            logger.error(
                f"Failure to mount {MNT_PATH}/images/efiboot.img on {IsoKindRhcos.MNT_EFIBOOT_PATH}"
            )
            raise RuntimeError("Failure to mount efiboot image")
        if not common_dpu.check_files(
            [
                "EFI/BOOT/BOOTAA64.EFI",
                "EFI/BOOT/grubaa64.efi",
            ],
            cwd=IsoKindRhcos.MNT_EFIBOOT_PATH,
            read_check=True,
        ):
            logger.error(f"Cannot find expected files in {MNT_PATH}/images/efiboot.img")
            raise RuntimeError("Cannot find expected files in efiboot image")

    def setup_tftp_files(self) -> None:
        shutil.copy(f"{MNT_PATH}/images/pxeboot/vmlinuz", f"{TFTP_PATH}/pxelinux")
        shutil.copy(f"{MNT_PATH}/images/pxeboot/initrd.img", f"{TFTP_PATH}/pxelinux")
        shutil.copy(
            f"{IsoKindRhcos.MNT_EFIBOOT_PATH}/EFI/BOOT/BOOTAA64.EFI",
            f"{TFTP_PATH}/",
        )
        shutil.copy(
            f"{IsoKindRhcos.MNT_EFIBOOT_PATH}/EFI/BOOT/grubaa64.efi",
            f"{TFTP_PATH}/",
        )
        shutil.copy(
            common_dpu.packaged_file("manifests/pxeboot/grub.cfg.rhcos"),
            f"{TFTP_PATH}/grub.cfg",
        )

    def setup_http_files(self, ctx: RunContext) -> None:
        ign_dir = f"{WWW_PATH}/ign"
        shutil.rmtree(ign_dir, ignore_errors=True)
        os.makedirs(ign_dir)
        ign_img = f"{MNT_PATH}/images/ignition.img"
        host.local.run(
            [
                "bash",
                "-e",
                "-o",
                "pipefail",
                "-c",
                f"gzip -dc {shlex.quote(ign_img)} | cpio -idmv && test -f ./config.ign",
            ],
            cwd=ign_dir,
            die_on_error=True,
        )

        ign_filename = f"{ign_dir}/config.ign"

        with open(ign_filename, "r") as f:
            ign = json.load(f)

        ign["passwd"]["users"] = [
            {
                "name": "core",
                # password "redhat" generated with
                #
                #   python -c "import crypt; print(crypt.crypt('redhat', crypt.mksalt(crypt.METHOD_SHA512)))"
                "passwordHash": "$6$BoXym4aPNIJBpFdH$lOaAqC0lHwf6vmiSoyLcOafDGtf6PdY.s91eVA4SDsKDLtwMDGrA9emYVuT9Ti5.FIIR7TRqCpWaM44pq137i/",
                "sshAuthorizedKeys": ctx.ssh_keys,
            },
        ]

        if ctx.dpu_name:
            ign["storage"]["files"].append(
                common_dpu.ignition_storage_file(
                    path="/etc/hostname",
                    contents=ctx.dpu_name,
                    encode="plain",
                )
            )
        ign["storage"]["files"].append(
            common_dpu.ignition_storage_file(
                path="/etc/NetworkManager/conf.d/89-marvell-tools-unmanaged-devices.conf",
                contents=nm_conf_unmanaged_devices(),
            )
        )
        ign["storage"]["files"].append(
            common_dpu.ignition_storage_file(
                path="/etc/NetworkManager/system-connections/enP2p3s0-dpu-host.nmconnection",
                mode=0o600,
                contents=nm_profile_nm_host(),
            )
        )
        ign["storage"]["files"].append(
            common_dpu.ignition_storage_file(
                path="/etc/NetworkManager/system-connections/enP2p2s0-dpu-secondary.nmconnection",
                mode=0o600,
                contents=nm_profile_nm_secondary(ctx),
            )
        )

        ign_json = json.dumps(ign, indent=2)
        for line in ign_json.splitlines(keepends=True):
            logger.info(f"ignition: {repr(line)}")

        common.json_dump(ign, ign_filename)


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
        "--dpu-dev",
        type=str,
        default=Config.dpu_dev,
        help='Optional argument for interface on the DPU to use. The DPU\'s interface must be connected to the "--dev" interface on the calling host where the DHCP server is started. This can be either "primary", "secondary", the MAC address of the interface or the index. Note that "secondary" is the same as index 0. "primary" corresponds to the highest index, for example if you configure 3 secondary ports it would be the zero-based index 3. The number of secondaries depends on the EPF configuration of the DPU. If it is not a MAC address, one of the first things the command does is reset the DPU to read the configured MAC addresses.',
    )
    parser.add_argument(
        "--host-path",
        type=str,
        default=Config.host_path,
        help="Optional argument where the host filesystem is mounted. Default is '/host'. Run podman with \"-v /:/host\".",
    )
    parser.add_argument(
        "--iso-kind",
        choices=["auto", "rhel", "rhcos"],
        default=Config.cfg_iso_kind,
        help='Specify the ISO kind. This can be either "rhel", "rhcos" or "auto" (the default). Only with "rhel" we generate a kickstart. The meaning of other options may differ depending on the ISO kind or they may be ignored altogether.',
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
        help='The static hostname of the DPU. With CoreOS defaults to unset, with RHEL defaults to "marvell-dpu". With "--host-mode" set to "rhel" or "coreos", this is also added to /etc/hosts alongside "dpu".',
    )
    parser.add_argument(
        "-W",
        "--console-wait",
        type=float,
        default=Config.console_wait,
        help='After installation is started, the tool will stay connected to the serial port for the specified amount of time. The benefit is that we see what happens in the output of the tool. The downside is that we cannot attach a second terminal to the serial port during that time. Defaults to 0 which means to stay connected until the program ends. You can force a close of the serial port by sending SIGUSR1 to the process. The console output is also written to "{host-path}/tmp/pxeboot-serial.*.log".',
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
    parser.add_argument(
        "--dhcp-restricted",
        choices=["auto", "yes", "no"],
        default=Config.cfg_dhcp_restricted,
        help='Control whether the DHCP server restricts requests to a specific MAC address. With "yes", the DHCP server only responds to the specific DPU MAC address that was detected, which is useful when running on a network with an existing DHCP server. With "no", the DHCP server responds to any PXEClient on the network. With "auto" (the default), the behavior is determined automatically based on the well known MAC address that shows up in the Marvell DPU\'s UEFI boot menu when the MAC address is not stable.',
    )

    args = parser.parse_args()

    try:
        dpu_dev = Config.validate_dpu_dev(args.dpu_dev)
    except ValueError:
        parser.error(
            'The dpu-dev is invalid. Must be "primary", "secondary" or a MAC address or a number'
        )

    cfg = Config(
        dpu_name=args.dpu_name,
        iso=args.iso,
        cfg_iso_kind=args.iso_kind,
        host_path=args.host_path,
        cfg_host_mode=args.host_mode,
        dev=args.dev,
        dpu_dev=dpu_dev,
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
        cfg_dhcp_restricted=args.dhcp_restricted,
    )

    if not common_dpu.check_files(
        (
            "tmp",
            "usr/bin",
        ),
        cwd=cfg.host_path,
    ):
        parser.error(
            f"The host_path {cfg.host_path} seems not valid. Use `podman run -v /:/host ...`?"
        )

    ctx = RunContext(cfg=cfg)

    return ctx


def is_marvell_random_mac(mac: str) -> bool:
    # By default, the MAC address is not stable. In the boot menu of the BIOS
    # those show up as MAC:80AA99887766 and MAC:80AA99887767.
    #
    # In those cases, at boot time the MAC address is random, which can cause
    # problems (for example, we cannot use "--dhcp-restrict=yes" and a RHCOS
    # installation might fail).
    #
    # Consider configuring stable MAC address (see "docs/howto_fix_mac_addresses.txt").
    return bool(re.search("^80:aa:99:88:77:6[67]$", mac))


def detect_dhcp_restricted(ctx: RunContext) -> bool:
    if ctx.cfg.cfg_dhcp_restricted != "auto":
        return common.str_to_bool(ctx.cfg.cfg_dhcp_restricted)

    dpu_mac, in_boot_menu = ctx.dpu_mac_ensure()
    return not is_marvell_random_mac(dpu_mac)


def detect_host_mode(*, host_path: str, iso_kind: Optional[IsoKind]) -> str:
    if iso_kind is not None and not isinstance(iso_kind, IsoKindRhel):
        return "ephemeral"
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


def ssh_cmd(ctx: RunContext, host_ip: str, *args: str) -> list[str]:
    return [
        "ssh",
        "-i",
        ctx.ssh_privkey_file,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=QUIET",
        f"{ctx.iso_kind.SSH_USER}@{host_ip}",
        *args,
    ]


def ssh_get_ipaddrs(ctx: RunContext, *, host_ip: str) -> Optional[list[str]]:
    ret = host.local.run(ssh_cmd(ctx, host_ip, "hostname", "-I"))
    if not ret:
        return []
    host_ips = set(ret.out.split())
    host_ips.discard(host_ip)
    return sorted(host_ips)


def check_ip_is_ready(ctx: RunContext, ips: list[str]) -> tuple[Optional[str], bool]:
    ip = netdev.wait_ping(*ips)
    if ip is None:
        return None, False

    ret = host.local.run(
        ssh_cmd(ctx, ip, "uptime"),
        log_level_result=logging.DEBUG,
    )
    if not ret:
        return ip, False

    return ip, True


def check_host_is_booted(ctx: RunContext) -> Optional[str]:
    ips_unique = set(common_dpu.DPU_DHCPRANGE)
    ips_unique.discard(common_dpu.dpu_ip4addr)
    ips = [common_dpu.dpu_ip4addr] + sorted(ips_unique)

    while True:
        ip, is_ready = check_ip_is_ready(ctx, ips)

        if ip is None:
            # No IP address is ready at all.
            return None

        if is_ready:
            # This IP address is ready.
            if ip != common_dpu.dpu_ip4addr:
                # Just re-check, whether our static IP addess would also be
                # ready and prefer that instead.
                ip2, is_ready2 = check_ip_is_ready(ctx, [common_dpu.dpu_ip4addr])
                if is_ready2:
                    return ip2
            return ip

        # This IP address replied to pings, but is not ready. Retry, but
        # without this IP.
        ips.remove(ip)


def wait_for_boot(ctx: RunContext) -> str:
    ser = ctx.serial_get()
    has_ser = True
    time_start = time.monotonic()
    timeout = max(ctx.cfg.console_wait + 100.0, 1800.0)
    logger.info(f"Wait for boot and IP address {common_dpu.dpu_ip4addr}")
    sleep_time = 60
    while True:

        if has_ser and (
            _signal_sigusr1_received
            or (
                ctx.cfg.console_wait > 0
                and time.monotonic() > time_start + ctx.cfg.console_wait
            )
        ):
            logger.info(f"Closing serial console {ser.port}")
            has_ser = False
            ser.close()

        # We rely on configuring a static IP address on the installed host.
        #
        # For one, to always have that IP address there (even after there
        # is no more DHCP server running) is useful to access the host.
        #
        # But also, if we would wait here to ping one of the DHCP addresses,
        # then we wouldn't easily know whether the installer is still running
        # or installation completed with successful. To find the static IP
        # address quite reliably tells us that the host is up.
        ip = check_host_is_booted(ctx)
        if ip is not None:
            logger.info(f"got response from {ip}")
            return ip

        if time.monotonic() > time_start + timeout:
            raise RuntimeError(
                f"Failed to detect booted Marvell DPU on {common_dpu.dpu_ip4addr} or DHCP range"
            )

        if has_ser:
            # Read and log the output for a bit longer. This way, we see how the
            # DPU starts installation.
            sleep_end_time = time.monotonic() + sleep_time
            while (
                (now := time.monotonic()) < sleep_end_time
            ) and not _signal_sigusr1_received:
                ser.sleep(min(2.0, sleep_end_time - now))
        else:
            time.sleep(sleep_time)

        sleep_time = max(int(sleep_time / 1.3), 9)


def create_serial(*, host_path: str) -> common.Serial:
    # We also write the data from the serial port to "{host_path}/tmp/pxeboot-serial-*.log"
    # on the host. For debugging, you can find what was written there.
    log_stream_filename = (
        f"{host_path}/tmp/pxeboot-serial.{datetime.datetime.now():%Y%m%d-%H%M%S.%f}.log"
    )

    logger.info(
        f"Select entry and boot in {common_dpu.TTYUSB0} (log to {log_stream_filename})"
    )

    log_stream = open(log_stream_filename, "ab", buffering=0)

    return common.Serial(
        common_dpu.TTYUSB0,
        log_stream=log_stream,
        own_log_stream=True,
    )


def uefi_boot_menu_process(
    ctx: RunContext,
    *,
    select_boot: Optional[str] = None,
) -> dict[int, str]:

    if select_boot:
        logger.info(f"Parse boot menu to start booting {select_boot!r}")
        assert netdev.validate_ethaddr_or_none(select_boot) is not None
    else:
        logger.info("Parse boot menu to find all MAC addresses")

    ser = ctx.serial_get()

    class ParsingState(enum.IntEnum):
        START = enum.auto()
        SAW_START_MARKER = enum.auto()
        PARSING = enum.auto()
        DONE = enum.auto()

    dpu_macs: dict[int, str] = {}

    search_count = 0
    MAX_SEARCH_COUNT = 50

    line_pattern = re.compile(
        "\x1b\\[0m\x1b\\[37m\x1b\\[40m([^\x1b]*)\x1b\\[0m\x1b\\[30m\x1b\\[47m",
        flags=re.DOTALL,
    )

    parsing_state = ParsingState.START

    ready_to_boot = False

    while parsing_state < ParsingState.DONE:

        if search_count >= MAX_SEARCH_COUNT:
            raise RuntimeError("Failure to parse boot entries (parsing did not end)")

        search_count += 1
        ser.send(KEY_DOWN)

        is_first_match = True
        found_mac: Optional[str] = None

        while parsing_state < ParsingState.DONE:
            try:
                line_match_full = ser.expect(
                    line_pattern,
                    0.80 if is_first_match else 0.0,
                    verbose=False,
                )
            except Exception:
                break

            # Usually, after a KEY_DOWN we only expect to find a single
            # "line_pattern". But if there were multiple patterns inside the
            # buffer, we would want to parse them all but care most about the
            # last one (that is where the cursor is). This is the purpose of
            # the loop and "is_first_match".
            is_first_match = False

            line_matches = re.finditer(line_pattern, line_match_full)
            for line_match in line_matches:
                (line_entry,) = line_match.groups()

                is_start_marker = re.search("^UEFI Misc Device$", line_entry)
                if is_start_marker:
                    # We need to detect wrap around in the menu. We take this
                    # "line_entry" as marker for that.
                    if parsing_state == ParsingState.START:
                        # We now see the start marker the first time. We are
                        # armed, but not yet fully.
                        parsing_state = ParsingState.SAW_START_MARKER
                    elif parsing_state == ParsingState.SAW_START_MARKER:
                        # We saw multiple start marker in a row. We keep waiting
                        # for a good line to start parsing.
                        pass
                    elif parsing_state == ParsingState.PARSING:
                        # We were parsing and saw another start marker. We are done.
                        parsing_state = ParsingState.DONE
                        break
                    continue

                pxe_line_match = re.search(
                    "^UEFI PXEv4 \\(MAC:([0-9a-fA-F]{12})\\)$",
                    line_entry,
                )
                if not pxe_line_match:
                    continue

                if parsing_state == ParsingState.SAW_START_MARKER:
                    # OK, we saw the start marker and were waiting to start. Now we
                    # are fully parsing.
                    parsing_state = ParsingState.PARSING
                elif parsing_state != ParsingState.PARSING:
                    # Not yet parsing. We must first wrap around in the menu.
                    continue

                (mac,) = pxe_line_match.groups()
                mac = ":".join(mac[i : i + 2] for i in range(0, len(mac), 2))
                mac = netdev.validate_ethaddr(mac)
                devidx = len(dpu_macs)
                logger.info(f"Found PXE boot entry {devidx!r} with MAC {mac!r}")
                dpu_macs[devidx] = mac

                found_mac = mac

        if select_boot is None:
            # We only parse the menu, don't try to boot. Continue parsing
            # until we are ParsingState.DONE.
            continue

        if select_boot != found_mac:
            # Our cursor is not the right MAC address.
            continue

        try:
            ser.expect(
                line_pattern,
                0.5,
                verbose=False,
            )
        except Exception:
            pass
        else:
            # We found another marker in the serial output. That must not be,
            # in any case, the cursor is not at the right location.
            raise RuntimeError(
                f"Failure to select boot entry for {select_boot!r} (unexpected menu item)"
            )

        ready_to_boot = True
        break

    if not dpu_macs:
        raise RuntimeError("Failure to parse boot entries (no PXE entries were found)")

    if select_boot is None:
        # Tap UP twice, so we are again above the start marker (see
        # "is_start_marker").  Note that we leave the menu here after detecting
        # the MAC addresses. The caller may decide to call the function again,
        # this time with a "select_boot" parameter to boot. We leave the menu
        # in a suitable state.
        ser.send(KEY_UP * 2, sleep=0.2)
        logger.info(f"Detected interfaces are {dpu_macs}")
        return dpu_macs

    if not ready_to_boot:
        logger.warn(
            f"Detected interfaces are {dpu_macs}. Cannot boot requested interface {select_boot!r}"
        )
        raise RuntimeError(
            f"Didn't find boot menu entry for PXE boot {select_boot!r} in BIOS. Detected interfaces are {dpu_macs}."
        )

    logger.info(
        f"Detected interfaces are (partial) {dpu_macs}. Booting now {select_boot!r}."
    )
    ser.send(KEY_ENTER, sleep=10)
    return dpu_macs


def uefi_reset_and_enter_boot_menu(ctx: RunContext) -> None:
    ser = ctx.serial_get()

    logger.info("Reset DPU and enter UEFI boot menu")

    reset()

    # Pop everything from the buffer first.
    ser.expect(".*")

    logger.info("waiting for instructions to access boot menu")
    ser.expect("Press 'B' within 10 seconds for boot menu", 30)
    ser.sleep(1)
    logger.info("Pressing B to access boot menu")
    ser.send("b")
    logger.info("waiting for instructions to Boot from Secondary Boot Device")
    ser.expect("2\\) Boot from Secondary Boot Device", 10)
    ser.sleep(1)
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
    ser.sleep(1)
    logger.info("pressing down again")
    ser.send(KEY_DOWN)
    logger.info("waiting for Boot manager entry")
    ser.expect("This selection will.*take you to the Boot.*Manager", 3)
    ser.send(KEY_ENTER)
    ser.expect("Device Path")


def uefi_enter_boot_menu_and_detect_dpu_macs(ctx: RunContext) -> dict[int, str]:
    # We do a reset, enter the UEFI boot menu and search it to detect
    # all DPU MAC addresses.
    #
    # Important, afterwards we are still inside the boot menu, to call
    # uefi_boot_menu_process() to boot an entry (without need to do a
    # full uefi_reset_and_enter_boot_menu() first).
    logger.info("Reset and enter boot menu to find all MAC addresses")
    uefi_reset_and_enter_boot_menu(ctx)
    dpu_macs = uefi_boot_menu_process(ctx)
    return dpu_macs


def uefi_enter_boot_menu_and_boot(ctx: RunContext) -> None:
    logger.info(f"Reset and enter boot menu to boot dpu-dev {ctx.cfg.dpu_dev!r}")

    dpu_mac, in_boot_menu = ctx.dpu_mac_ensure(reuse_serial_context=True)

    if in_boot_menu:
        # While fetching the "dpu_mac", we also needed to fetch the "dpu_macs",
        # which already left us inside the boot menu. We are already there. We
        # don't need to reset again.
        pass
    else:
        uefi_reset_and_enter_boot_menu(ctx)

    # Boot the entry.
    uefi_boot_menu_process(ctx, select_boot=dpu_mac)


def detect_dpu_mac(
    ctx: RunContext,
    *,
    reuse_serial_context: bool,
) -> tuple[str, bool]:
    in_boot_menu = False
    real_dpu_mac = netdev.validate_ethaddr_or_none(ctx.cfg.dpu_dev)
    if real_dpu_mac is not None:
        logger.info(
            f"dpu-mac-detect: skip full detection as dpu_dev is full MAC {real_dpu_mac!r}"
        )
    else:
        logger.info(
            f"dpu-mac-detect: parse all MAC addresses from BIOS menu to determine MAC for dpu-dev {ctx.cfg.dpu_dev!r}"
        )
        if reuse_serial_context:
            # We use the serial context created by the caller. In that case,
            # if dpu_macs_ensure() ends up entering the boot menu, we want to
            # stay there (and indicate that to the caller).
            dpu_macs, in_boot_menu = ctx.dpu_macs_ensure()
        else:
            # We create a new serial context. The caller does not care whehter we
            # stay inside the boot menu.
            with ctx.serial_open():
                dpu_macs, _ = ctx.dpu_macs_ensure()

        if ctx.cfg.dpu_dev == "primary":
            real_dpu_mac = dpu_macs[max(dpu_macs)]
        elif ctx.cfg.dpu_dev == "secondary":
            real_dpu_mac = dpu_macs[min(dpu_macs)]
        else:
            try:
                real_dpu_mac = dpu_macs[int(ctx.cfg.dpu_dev)]
            except (KeyError, ValueError):
                raise RuntimeError(
                    f"Cannot find boot entry for {ctx.cfg.dpu_dev!r}. Detected interfaces are {dpu_macs}"
                )

        logger.info(
            f"dpu-mac-detect: detected MAC addresses {real_dpu_mac!r} for dpu-dev {ctx.cfg.dpu_dev!r} (MACs are {dpu_macs})"
        )

        if ctx.before_prompt:
            # We have "--prompt" option enabled, and are still before prompting.
            #
            # With prompting, I think the user will want to so something with the DPU. Don't
            # let it hang in the boot menu, but reset.
            #
            # If we do so, we are also no longer "in_boot_menu".
            #
            # Note that we ignore "reuse_serial_context" for this. For one, in
            # practice "reuse_serial_context" is always False if we are still
            # before prompting. But regardless, that parameter is a request to
            # leave the DPU in the boot menu if possible. It is not an absolute
            # requirement and we can leave it in undefined (not "in_boot_menu")
            # state.
            in_boot_menu = False
            reset()

    if is_marvell_random_mac(real_dpu_mac):
        logger.warning(
            "The MAC address on the Marvell DPU seems not stable. This might cause problems later."
        )

    return real_dpu_mac, in_boot_menu


def write_hosts_entry(ctx: RunContext) -> None:
    entries: collections.abc.Mapping[
        str,
        tuple[str, Optional[collections.abc.Iterable[str]]],
    ]
    if ctx.dpu_name:
        entries = {ctx.dpu_name: (common_dpu.dpu_ip4addr, ["dpu"])}
    else:
        entries = {"dpu": (common_dpu.dpu_ip4addr, None)}

    common.etc_hosts_update_file(
        entries,
        f"{ctx.cfg.host_path}/etc/hosts",
    )


def post_pxeboot(ctx: RunContext) -> None:
    if ctx.host_mode_persist:
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

    ctx.iso_kind.setup_http_files(ctx)

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
    common_dpu.run_process(
        "tftp",
        f"/usr/sbin/in.tftpd -v -v -s -B 1468 -L {shlex.quote(TFTP_PATH)}",
    )
    ctx.iso_kind.setup_tftp_files()


def prepare_ssh_keys(ctx: RunContext) -> tuple[list[str], str]:
    logger.info("Configure ssh-keys")

    ssh_keys = []

    ssh_privkey_file = common.unwrap(
        common_dpu.ssh_generate_key(
            file="/tmp/marvell-tools/id_ed25519",
            create=True,
            comment="pxeboot-internal@marvel-tools.local",
        )
    )
    ssh_keys.append(common_dpu.ssh_read_pubkey(ssh_privkey_file))

    add_host_key = True
    if ctx.cfg.cfg_ssh_keys:
        add_host_key = False
        for s in ctx.cfg.cfg_ssh_keys:
            if not s:
                add_host_key = True
            else:
                ssh_keys.append(s)

    if add_host_key:
        privkey_file = common.unwrap(
            common_dpu.ssh_generate_key(
                file=f"{ctx.cfg.host_path}/root/.ssh/id_ed25519",
                create=True,
            )
        )
        logger.info(f"prepare-host: add host key {repr(privkey_file)}")
        ssh_keys.append(common_dpu.ssh_read_pubkey(privkey_file))

    if not ssh_keys:
        logger.info("prepare-host: no SSH keys")
    else:
        for k in ssh_keys:
            logger.info(f"prepare-host: use SSH key {repr(k)}")

    return ssh_keys, ssh_privkey_file


def prepare_host(ctx: RunContext) -> None:
    logger.info("Configure host for Pxeboot")
    if ctx.host_mode_persist:
        common_dpu.nmcli_setup_mngtiface(
            ifname=ctx.cfg.dev,
            chroot_path=ctx.cfg.host_path,
            ip4addr=common_dpu.host_ip4addrnet,
        )
    else:

        def _cleanup() -> None:
            host.local.run(
                f"ip addr del {shlex.quote(common_dpu.host_ip4addrnet)} dev {shlex.quote(ctx.cfg.dev)}"
            )

        common_dpu.global_cleanup.add(_cleanup)
        host.local.run(
            f"ip addr add {shlex.quote(common_dpu.host_ip4addrnet)} dev {shlex.quote(ctx.cfg.dev)}"
        )

    if not ctx.host_mode_persist:
        common_dpu.global_cleanup.add(
            lambda: common_dpu.nft_masquerade(
                ifname=ctx.cfg.dev,
                subnet=None,
            )
        )
    common_dpu.nft_masquerade(ifname=ctx.cfg.dev, subnet=common_dpu.dpu_subnet)

    host.local.run("sysctl -w net.ipv4.ip_forward=1")


def setup_dhcp(ctx: RunContext) -> None:
    dhcp_restricted = ctx.dhcp_restricted_ensure()
    hardware_ethernet: Optional[str] = None
    if dhcp_restricted:
        hardware_ethernet, in_boot_menu = ctx.dpu_mac_ensure()

    common_dpu.run_dhcpd(
        dhcpd_conf=common_dpu.packaged_file("manifests/pxeboot/dhcpd.conf"),
        pxe_filename=ctx.iso_kind.DHCP_PXE_FILENAME,
        hardware_ethernet=hardware_ethernet,
        dhcp_restricted=dhcp_restricted,
    )


def create_and_mount_iso(ctx: RunContext) -> IsoKind:
    host.local.run(["umount", IsoKindRhcos.MNT_EFIBOOT_PATH])
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
            iso_kind = IsoKind.detect_from_iso(
                cfg_iso_kind=ctx.cfg.cfg_iso_kind,
                read_check=True,
            )
            if iso_kind is not None:
                logger.info(
                    f"ISO {iso_path} successfully mounted at {MNT_PATH} (as {iso_kind})"
                )
                iso_kind.mount_nested_iso()
                return iso_kind
            host.local.run(["umount", MNT_PATH])
            logger.warning(
                f"ISO {iso_path} does not look like and ISO kind {ctx.cfg.cfg_iso_kind!r}"
            )

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


def dpu_pxeboot(ctx: RunContext) -> str:
    logger.info(f"Start PXE boot with dpu-dev {ctx.cfg.dpu_dev!r}")
    with ctx.serial_open():
        uefi_enter_boot_menu_and_boot(ctx)
        ip = wait_for_boot(ctx)
    return ip


def main() -> None:
    signal.signal(signal.SIGUSR1, _signal_handler)

    ctx = parse_args()

    common_dpu.global_cleanup.add(ctx.ssh_privkey_file_cleanup)

    logger.info(f"pxeboot: {shlex.join(shlex.quote(s) for s in sys.argv)}")
    logger.info(f"pxeboot run context: {ctx}")

    iso_kind: Optional[IsoKind] = None
    if not ctx.cfg.host_setup_only:
        iso_kind = create_and_mount_iso(ctx)
    else:
        iso_kind = (
            IsoKind.detect_from_iso(
                cfg_iso_kind=ctx.cfg.cfg_iso_kind,
                check_mount=False,
            )
            or IsoKindRhel()
        )
    ctx.iso_kind_set_once(iso_kind)

    host_mode = ctx.cfg.cfg_host_mode
    if host_mode == "auto":
        host_mode = detect_host_mode(host_path=ctx.cfg.host_path, iso_kind=iso_kind)
    ctx.host_mode_set_once(host_mode)

    ssh_keys, ssh_privkey_file = prepare_ssh_keys(ctx)
    ctx.ssh_keys_set_once(ssh_keys)
    ctx.ssh_privkey_file_set_once(ssh_privkey_file)

    prepare_host(ctx)

    if not ctx.cfg.host_setup_only:

        setup_dhcp(ctx)
        setup_tftp(ctx)
        setup_http(ctx)

        logger.info("Giving services time to settle")
        time.sleep(3)

        common_dpu.check_services_running()

        if ctx.cfg.prompt:
            try:
                input(
                    "dhcp/tftp/http services started. Waiting. Press ENTER to continue or abort with CTRL+C"
                )
            except KeyboardInterrupt:
                sys.exit(0)

        ctx.before_prompt_set_after()

        for try_count in itertools.count(start=1):
            logger.info(f"Starting UEFI PXE Boot (try {try_count})")
            try:
                host_ip = dpu_pxeboot(ctx)
            except Exception as e:
                if try_count >= 3:
                    raise RuntimeError(f"Failure to pxeboot: {e}") from e
                logger.warning(f"Failure to pxeboot (try {try_count}): {e}")
                continue
            break

    post_pxeboot(ctx)

    host_setup_only_msg = ""
    host_ips_msg = ""

    if ctx.cfg.host_setup_only:
        host_setup_only_msg = " (host-setup-only)"
    else:
        other_host_ips = ssh_get_ipaddrs(ctx, host_ip=host_ip)
        if other_host_ips:
            host_ips_msg = f" (or on {list(other_host_ips)}"

    logger.info("Terminating http, tftp, and dhcpd")
    common_dpu.global_cleanup.cleanup()

    logger.info(
        f"SUCCESS{host_setup_only_msg}. Try `ssh {ctx.iso_kind.SSH_USER}@{host_ip}`{host_ips_msg}"
    )


if __name__ == "__main__":
    common_dpu.run_main(main)
