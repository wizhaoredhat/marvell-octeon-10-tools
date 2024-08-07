#!/usr/bin/env python3

import argparse
import http.server
import os
import pexpect
import shlex
import shutil
import time

from collections.abc import Iterable
from multiprocessing import Process
from typing import Optional

from ktoolbox import common
from ktoolbox import host

import common_dpu

from common_dpu import ESC
from common_dpu import KEY_DOWN
from common_dpu import KEY_ENTER
from common_dpu import minicom_cmd
from common_dpu import run
from reset import reset


children = []
iso_mount_path = "/mnt/marvel_dpu_iso"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process ISO file.")
    parser.add_argument(
        "iso",
        type=str,
        nargs="?",
        default="rhel:9.4",
        help='Select the RHEL ISO to install. This can be a file name (make sure to map the host with `-v /:/host` and specify the path name starting with "/host"); it can be a HTTP URL (in which case the file will be downloaded to /host/root/rhel-iso-$NAME if such file does not exist yet); it can also be "rhel:9.x" which will automatically detect the right HTTP URL to download the latest iso. Default: "rhel:" to choose a recent RHEL version',
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
        nargs="+",
        help='Specify SSH public keys to add to the DPU\'s /root/.ssh/authorized_keys. Can be specified multiple times. If unspecified or set to "", include "/{host-path}/root/.ssh/id_ed25519.pub" (this file will be generated if it doesn\'t exist).',
    )
    parser.add_argument(
        "--yum-repos",
        choices=["none", "rhel-nightly"],
        default="none",
        help='We generate "/etc/yum.repos.d/marvell-tools-beaker.repo" with latest RHEL9 nightly compose. However, that repo is disabled unless "--yum-repos=rhel-nightly".',
    )

    return parser.parse_args()


def ping(hn: str) -> bool:
    ping_cmd = f"timeout 1 ping -4 -c 1 {hn}"
    return run(ping_cmd).returncode == 0


def wait_any_ping(hn: Iterable[str], timeout: float) -> str:
    print("Waiting for response from ping")
    begin = time.time()
    end = begin
    hn = list(hn)
    while end - begin < timeout:
        for e in hn:
            if ping(e):
                return e
        time.sleep(5)
        end = time.time()
    raise Exception(f"No response after {round(end - begin, 2)}s")


def wait_for_boot() -> None:
    time.sleep(1000)
    try:
        candidates = [f"172.131.100.{x}" for x in range(10, 21)]
        response_ip = wait_any_ping(candidates, 12000)
        print(f"got response from {response_ip}")
    except Exception as e:
        print("Failed to detect IP from Marvell card")
        raise e


def select_pxe_entry() -> None:
    print("selecting pxe entry")

    run("pkill -9 minicom")
    print("spawn minicom")
    child = pexpect.spawn(minicom_cmd("/dev/ttyUSB0"))
    child.maxread = 10000
    print("waiting for instructions to access boot menu")
    child.expect("Press 'B' within 10 seconds for boot menu", 30)
    time.sleep(1)
    print("Pressing B to access boot menu")
    child.send("b")
    print("waiting for instructions to Boot from Secondary Boot Device")
    child.expect("2\\) Boot from Secondary Boot Device", 10)
    time.sleep(1)
    child.send("2")
    print("waiting to escape to UEFI boot menu")
    child.expect("Press ESCAPE for boot options", 60)
    print("Sending escape 5 times")
    child.send(ESC * 5)
    print("waiting on language option")
    child.expect(
        "This is the option.*one adjusts to change.*the language for the.*current system",
        timeout=3,
    )
    print("pressing down")
    child.send(KEY_DOWN)
    time.sleep(1)
    print("pressing down again")
    child.send(KEY_DOWN)
    print("waiting for Boot manager entry")
    child.expect("This selection will.*take you to the Boot.*Manager", timeout=3)
    child.send(KEY_ENTER)
    child.expect("Device Path")
    retry = 30
    print(f"Trying up to {retry} times to find pxe boot interface")
    while retry:
        child.send(KEY_DOWN)
        time.sleep(0.1)
        try:
            # TODO: FIXME: We need to read the port configuration.
            # e.g. 80AA99887766 + number of lanes used in the SERDES
            child.expect("UEFI PXEv4.*MAC:80AA99887767", timeout=1)
            break
        except Exception:
            retry -= 1
    if not retry:
        e = Exception("Didn't find boot interface")
        print(e)
        raise e
    else:
        print(f"Found boot interface after {30 - retry} tries, sending enter")
        child.send(KEY_ENTER)
        time.sleep(10)
        # Use the ^ and v keys to select which entry is highlighted.
        # Press enter to boot the selected OS, `e' to edit the commands
        # before booting or `c' for a command-line.
        # time.sleep(1)
        # timeout = 30

    child.close()
    print("Closing minicom")


def write_hosts_entry(host_path: str) -> None:
    common.etc_hosts_update_file(
        {
            "dpu": (common_dpu.dpu_ip4addr, None),
        },
        f"{host_path}/etc/hosts",
    )


def post_pxeboot(host_path: str) -> None:
    write_hosts_entry(host_path)


def copy_kickstart(host_path: str, ssh_pubkey: list[str], yum_repos: str) -> None:
    with open(common_dpu.packaged_file("manifests/pxeboot/kickstart.ks"), "r") as f:
        kickstart = f.read()

    yum_repo_enabled = yum_repos == "rhel-nightly"

    kickstart = kickstart.replace(
        "@__SSH_PUBKEY__@", shlex.quote("\n".join(ssh_pubkey))
    )
    kickstart = kickstart.replace("@__DPU_IP4ADDRNET__@", common_dpu.dpu_ip4addrnet)
    kickstart = kickstart.replace("@__HOST_IP4ADDR__@", common_dpu.host_ip4addr)
    kickstart = kickstart.replace("@__YUM_REPO_URL__@", shlex.quote(""))
    kickstart = kickstart.replace(
        "@__YUM_REPO_ENABLED__@", shlex.quote("1" if yum_repo_enabled else "0")
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


def setup_http(host_path: str, ssh_pubkey: list[str], yum_repos: str) -> None:
    os.makedirs("/www", exist_ok=True)
    run(f"ln -s {iso_mount_path} /www")

    copy_kickstart(host_path, ssh_pubkey, yum_repos)

    def http_server() -> None:
        os.chdir("/www")
        server_address = ("", 80)
        handler = http.server.SimpleHTTPRequestHandler
        httpd = http.server.HTTPServer(server_address, handler)
        httpd.serve_forever()

    p = Process(target=http_server)
    p.start()
    children.append(p)


def setup_tftp() -> None:
    print("Configuring TFTP")
    os.makedirs("/var/lib/tftpboot/pxelinux", exist_ok=True)
    print("starting in.tftpd")
    run("killall in.tftpd")
    p = common_dpu.run_process("/usr/sbin/in.tftpd -s -B 1468 -L /var/lib/tftpboot")
    children.append(p)
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


def prepare_host(dev: str, host_path: str, ssh_key: Optional[list[str]]) -> list[str]:
    common_dpu.nmcli_setup_mngtiface(
        ifname=dev,
        chroot_path=host_path,
        ip4addr=common_dpu.host_ip4addrnet,
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
        ssh_privkey_file = common_dpu.ssh_generate_key(host_path)
        if ssh_privkey_file is not None:
            ssh_pubkey.append(common_dpu.ssh_read_pubkey(ssh_privkey_file))

    return ssh_pubkey


def setup_dhcp() -> None:
    print("Configuring DHCP")
    shutil.copy(
        common_dpu.packaged_file("manifests/pxeboot/dhcpd.conf"), "/etc/dhcp/dhcpd.conf"
    )
    run("killall dhcpd")
    p = common_dpu.run_process(
        "/usr/sbin/dhcpd -f -cf /etc/dhcp/dhcpd.conf -user dhcpd -group dhcpd"
    )
    children.append(p)


def mount_iso(iso_path: str) -> None:
    os.makedirs(iso_mount_path, exist_ok=True)
    run(f"umount {iso_mount_path}")
    run(f"mount -t iso9660 -o loop {iso_path} {iso_mount_path}")


def main() -> None:
    args = parse_args()
    print("Preparing services for Pxeboot")
    ssh_pubkey = prepare_host(args.dev, args.host_path, args.ssh_key)
    iso_path = common_dpu.create_iso_file(args.iso, chroot_path=args.host_path)
    setup_dhcp()
    mount_iso(iso_path)
    setup_tftp()
    setup_http(args.host_path, ssh_pubkey, args.yum_repos)
    print("Giving services time to settle")
    time.sleep(10)
    print("Starting UEFI PXE Boot")
    print("Resetting card")
    reset()
    select_pxe_entry()
    wait_for_boot()
    post_pxeboot(args.host_path)
    print("Terminating http, tftp, and dhcpd")
    for e in children:
        e.terminate()
    print("SUCCESS. Try `ssh root@dpu`")


if __name__ == "__main__":
    main()
