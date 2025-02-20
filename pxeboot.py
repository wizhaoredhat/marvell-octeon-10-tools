#!/usr/bin/env python3

import argparse
import os
import shlex
import shutil
import sys
import time

from typing import Optional

from ktoolbox import common
from ktoolbox import host

import common_dpu

from common_dpu import ESC
from common_dpu import KEY_DOWN
from common_dpu import KEY_ENTER
from common_dpu import logger
from reset import reset


iso_mount_path = "/mnt/marvel_dpu_iso"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process ISO file.")
    parser.add_argument(
        "iso",
        type=str,
        nargs="?",
        default="rhel:",
        help=f'Select the RHEL ISO to install. This can be a file name (make sure to map the host with `-v /:/host` and specify the path name starting with "/host"); it can be a HTTP URL (in which case the file will be downloaded to /host/root/rhel-iso-$NAME if such file does not exist yet); it can also be "rhel:9.x" which will automatically detect the right HTTP URL to download the latest iso. Default: "rhel:" to choose RHEL version {common_dpu.DEFAULT_RHEL_ISO}.',
    )
    parser.add_argument(
        "--dev",
        type=str,
        default="eno4",
        help="Optional argument of type string for device. Default is 'eno4'.",
    )
    parser.add_argument(
        "--host-path",
        type=str,
        default="/host",
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
        default="none",
        help='We generate "/etc/yum.repos.d/marvell-tools-beaker.repo" with latest RHEL9 nightly compose. However, that repo is disabled unless "--yum-repos=rhel-nightly".',
    )
    parser.add_argument(
        "--host-mode",
        choices=["auto", "rhel", "coreos"],
        default="auto",
        help='How to treat the host. With "rhel" we configure a (persisted) NetworkManager connection profile for device (eno4). With "coreos", this only configures an ad-hoc IP address with iproute. Port forwarding is always ephemeral via nft rules.',
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
        default="marvell-dpu",
        help='The static hostname of the DPU. Defaults to "marvell-dpu". With "--host-mode=rhel" this is also added to /etc/hosts alongside "dpu".',
    )
    parser.add_argument(
        "--nm-secondary-cloned-mac-address",
        type=str,
        default="",
        help='The MAC address to configure on the "enP2p2s0-dpu-secondary" profile.',
    )
    parser.add_argument(
        "--nm-secondary-ip-address",
        type=str,
        default="",
        help='If set, configure a static ipv4.addresses on the profile "enP2p2s0-dpu-secondary". This should contain the subnet, for example "192.168.122.5/24".',
    )
    parser.add_argument(
        "--nm-secondary-ip-gateway",
        type=str,
        default="",
        help='If set, configure ipv4.gateway on the "enP2p2s0-dpu-secondary" (requires "--nm-secondary-ip-address"). This should be in the same subnet as the address.',
    )
    parser.add_argument(
        "-P",
        "--prompt",
        action="store_true",
        help="If set, start DHCP/TFTP/HTTP services and wait for the user to press ENTER. This can be used to manually boot via PXE.",
    )
    parser.add_argument(
        "-i",
        "--extra-package",
        default=[],
        action="append",
        help="List of extra packages that are installed during kickstart.",
    )
    parser.add_argument(
        "--default-extra-packages",
        action="store_true",
        help="If true, install additional default packages during kickstart. See '@__DEFAULT_EXTRA_PACKAGES__@' in \"manifests/pxeboot/kickstart.ks\".",
    )

    return parser.parse_args()


def detect_host_mode(host_path: str, host_mode: str) -> str:
    if host_mode == "auto":
        if host.local.run(
            [
                "grep",
                "-q",
                'NAME="Red Hat Enterprise Linux"',
                f"{host_path}/etc/os-release",
            ]
        ).success:
            host_mode = "rhel"
        else:
            host_mode = "coreos"
    return host_mode


def wait_for_boot() -> None:
    logger.info(f"Wait for boot and IP address {common_dpu.dpu_ip4addr}")
    end = time.monotonic() + 1800
    sleep_time = 60
    while True:
        time.sleep(sleep_time)
        sleep_time = max(int(sleep_time / 1.3), 9)
        if common_dpu.ping(common_dpu.dpu_ip4addr):
            logger.info(f"got response from {common_dpu.dpu_ip4addr}")
            break
        if time.monotonic() > end:
            raise RuntimeError(
                f"Failed to detect IP {common_dpu.dpu_ip4addr} on Marvell card"
            )


def select_pxe_entry() -> None:
    logger.info("selecting pxe entry")

    with common.Serial(common_dpu.TTYUSB0) as ser:
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
            time.sleep(0.1)
            try:
                # TODO: FIXME: We need to read the port configuration.
                # e.g. 80AA99887766 + number of lanes used in the SERDES
                ser.expect("UEFI PXEv4.*MAC:80AA99887767", 1)
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

        # Read and log the output for a bit longer. This way, we see how the
        # DPU starts installation.
        ser.expect(pattern=None, timeout=60)
        logger.info(f"Closing serial console {ser.port}")


def write_hosts_entry(host_path: str, dpu_name: str) -> None:
    common.etc_hosts_update_file(
        {
            dpu_name: (common_dpu.dpu_ip4addr, ["dpu"]),
        },
        f"{host_path}/etc/hosts",
    )


def post_pxeboot(host_mode: str, host_path: str, dpu_name: str) -> None:
    if host_mode == "rhel":
        write_hosts_entry(host_path, dpu_name)


def detect_yum_repo_url() -> str:
    res = host.local.run(
        [
            "sed",
            "-n",
            "s/^name=Red Hat Enterprise Linux \\([0-9]\\+\\.[0-9]\\+\\).0$/\\1/p",
            f"{iso_mount_path}/media.repo",
        ]
    )
    if res.success and res.out:
        os_version = res.out.splitlines()[-1].strip()
        url_base = (
            "http://download.hosts.prod.upshift.rdu2.redhat.com/rhel-9/composes/RHEL-9/"
        )
        sed_pattern = f's/.*href="\\(RHEL-{os_version}.0-updates[^"]*\\)".*/\\1/p'
        res = host.local.run(
            f"curl -s {shlex.quote(url_base)} | "
            f"sed -n {shlex.quote(sed_pattern)} | "
            "grep -v delete-me/ | sort | tail -n1"
        )
        if res.success:
            part = res.out.strip()
            if part:
                return f"{url_base}{part}"
    return ""


def copy_kickstart(
    host_path: str,
    dpu_name: str,
    ssh_pubkey: list[str],
    yum_repos: str,
    nm_secondary_cloned_mac_address: str,
    nm_secondary_ip_address: str,
    nm_secondary_ip_gateway: str,
    extra_package: list[str],
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
        "@__SSH_PUBKEY__@", shlex.quote("\n".join(ssh_pubkey))
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
        "@__YUM_REPO_ENABLED__@", shlex.quote("1" if yum_repo_enabled else "0")
    )
    kickstart = kickstart.replace(
        "@__EXTRA_PACKAGES__@",
        " ".join(shlex.quote(s) for s in extra_package),
    )
    kickstart = kickstart.replace(
        "@__DEFAULT_EXTRA_PACKAGES__@",
        "1" if default_extra_packages else "0",
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

    with open("/www/kickstart.ks", "w") as f:
        f.write(kickstart)


def setup_http(
    host_path: str,
    dpu_name: str,
    ssh_pubkey: list[str],
    yum_repos: str,
    nm_secondary_cloned_mac_address: str,
    nm_secondary_ip_address: str,
    nm_secondary_ip_gateway: str,
    extra_package: list[str],
    default_extra_packages: bool,
) -> None:
    os.makedirs("/www", exist_ok=True)
    host.local.run(f"ln -s {shlex.quote(iso_mount_path)} /www")

    copy_kickstart(
        host_path,
        dpu_name,
        ssh_pubkey,
        yum_repos,
        nm_secondary_cloned_mac_address,
        nm_secondary_ip_address,
        nm_secondary_ip_gateway,
        extra_package,
        default_extra_packages,
    )

    common_dpu.run_process(
        "httpd",
        [
            sys.executable,
            "-m",
            "http.server",
            "-d",
            "/www",
            "24380",
        ],
    )


def setup_tftp() -> None:
    logger.info("Configuring TFTP")
    os.makedirs("/var/lib/tftpboot/pxelinux", exist_ok=True)
    logger.info("starting in.tftpd")
    host.local.run("killall in.tftpd")
    common_dpu.run_process("tftp", "/usr/sbin/in.tftpd -s -B 1468 -L /var/lib/tftpboot")
    shutil.copy(
        f"{iso_mount_path}/images/pxeboot/vmlinuz", "/var/lib/tftpboot/pxelinux"
    )
    shutil.copy(
        f"{iso_mount_path}/images/pxeboot/initrd.img", "/var/lib/tftpboot/pxelinux"
    )
    shutil.copy(f"{iso_mount_path}/EFI/BOOT/grubaa64.efi", "/var/lib/tftpboot/")
    os.chmod("/var/lib/tftpboot/grubaa64.efi", 0o744)
    shutil.copy(
        common_dpu.packaged_file("manifests/pxeboot/grub.cfg"),
        "/var/lib/tftpboot/grub.cfg",
    )


def prepare_host(
    host_mode: str,
    dev: str,
    host_path: str,
    ssh_key: Optional[list[str]],
) -> list[str]:
    if host_mode == "rhel":
        common_dpu.nmcli_setup_mngtiface(
            ifname=dev,
            chroot_path=host_path,
            ip4addr=common_dpu.host_ip4addrnet,
        )
    else:
        host.local.run(
            f"ip addr add {shlex.quote(common_dpu.host_ip4addrnet)} dev {shlex.quote(dev)}"
        )

    common_dpu.nft_masquerade(ifname=dev, subnet=common_dpu.dpu_subnet)
    host.local.run("sysctl -w net.ipv4.ip_forward=1")

    ssh_pubkey = []

    add_host_key = True
    if ssh_key:
        add_host_key = False
        for s in ssh_key:
            if not s:
                add_host_key = True
            else:
                ssh_pubkey.append(s)

    if add_host_key:
        ssh_privkey_file = common_dpu.ssh_generate_key(
            host_path,
            create=True,
        )
        if ssh_privkey_file is not None:
            logger.info(f"prepare-host: add host key {repr(ssh_privkey_file)}")
            ssh_pubkey.append(common_dpu.ssh_read_pubkey(ssh_privkey_file))

    if not ssh_pubkey:
        logger.info("prepare-host: no SSH keys")
    else:
        for k in ssh_pubkey:
            logger.info(f"prepare-host: use SSH key {repr(k)}")

    return ssh_pubkey


def setup_dhcp() -> None:
    logger.info("Configuring DHCP")
    shutil.copy(
        common_dpu.packaged_file("manifests/pxeboot/dhcpd.conf"), "/etc/dhcp/dhcpd.conf"
    )
    host.local.run("killall dhcpd")
    common_dpu.run_process(
        "dhcpd",
        "/usr/sbin/dhcpd -f -cf /etc/dhcp/dhcpd.conf -user dhcpd -group dhcpd",
    )


def mount_iso(iso_path: str) -> None:
    os.makedirs(iso_mount_path, exist_ok=True)
    host.local.run(f"umount {shlex.quote(iso_mount_path)}")
    host.local.run(
        f"mount -t iso9660 -o loop {shlex.quote(iso_path)} {shlex.quote(iso_mount_path)}"
    )


def main() -> None:
    args = parse_args()
    host_mode = detect_host_mode(args.host_path, args.host_mode)
    logger.info("Preparing services for Pxeboot")
    ssh_pubkey = prepare_host(host_mode, args.dev, args.host_path, args.ssh_key)

    if not args.host_setup_only:
        iso_path = common_dpu.create_iso_file(args.iso, chroot_path=args.host_path)
        setup_dhcp()
        mount_iso(iso_path)
        setup_tftp()
        setup_http(
            args.host_path,
            args.dpu_name,
            ssh_pubkey,
            args.yum_repos,
            args.nm_secondary_cloned_mac_address,
            args.nm_secondary_ip_address,
            args.nm_secondary_ip_gateway,
            args.extra_package,
            args.default_extra_packages,
        )
        logger.info("Giving services time to settle")
        time.sleep(3)

        common_dpu.check_services_running()

        if args.prompt:
            input(
                "dhcp/tftp/http services started. Waiting. Press ENTER to continue or abort with CTRL+C"
            )
        logger.info("Starting UEFI PXE Boot")
        logger.info("Resetting card")
        reset()
        select_pxe_entry()
        wait_for_boot()

    post_pxeboot(host_mode, args.host_path, args.dpu_name)

    logger.info("Terminating http, tftp, and dhcpd")
    common.thread_list_join_all()

    if args.host_setup_only:
        host_setup_only = " (host-setup-only)"
    else:
        host_setup_only = ""

    logger.info(f"SUCCESS{host_setup_only}. Try `ssh root@{common_dpu.dpu_ip4addr}`")


if __name__ == "__main__":
    main()
