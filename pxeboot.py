import argparse
import os
import signal
import sys
from multiprocessing import Process
import threading
import pexpect
import time
import io
import shutil
import http.server
import requests

from reset import reset
from common_dpu import run, minicom_cmd


children = []
iso_mount_path = "/mnt/rhel_9_2_iso"
iso_path = "/root/RHEL-9.2.0-20230531.18-aarch64-dvd1.iso"

def wait_any_ping(hn, timeout):
    print("Waiting for response from ping")
    begin = time.time()
    end = begin
    while end - begin < timeout:
        for e in hn:
            if ping(e):
                return e
        time.sleep(5)
        end = time.time()
    raise Exception(f"No response after {round(end - begin, 2)}s")


def ping(hn):
    ping_cmd = f"timeout 1 ping -4 -c 1 {hn}"
    return run(ping_cmd).returncode == 0


def wait_for_boot():
    try:
        candidates = list(f"172.31.100.{x}" for x in range(10, 21))
        response_ip = wait_any_ping(candidates, 12000)
        print(f"got response from {response_ip}")
    except Exception as e:
        print("Failed to detect IP from Marvell card")
        raise e

def select_pxe_entry():
    print("selecting pxe entry")
    ESC = "\x1b"
    KEY_DOWN = '\x1b[B'
    KEY_ENTER = '\r\n'

    run("pkill -9 minicom")
    print("spawn minicom")
    child = pexpect.spawn(minicom_cmd("/dev/ttyUSB0"))
    child.maxread = 10000
    print("waiting for instructions to access boot menu")
    child.expect("Press 'B' within 10 seconds for boot menu", 30)
    time.sleep(1)
    print("Pressing B to access boot menu")
    child.send('b')
    print("waiting for instructions to Boot from Secondary Boot Device")
    child.expect("2\) Boot from Secondary Boot Device", 10)
    time.sleep(1)
    child.send('2')
    print("waiting to escape to UEFI boot menu")
    child.expect("Press ESCAPE for boot options", 60)
    print("Sending escape 5 times")
    child.send(ESC*5)
    print("waiting on language option")
    child.expect("This is the option.*one adjusts to change.*the language for the.*current system", timeout=3)
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
            child.expect("UEFI PXEv4.*MAC:80AA9988776A", timeout=1)
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

def uefi_pxe_boot():
    print("Starting UEFI PXE Boot")
    print("Resetting card")
    reset()
    select_pxe_entry()
    wait_for_boot()

def http_server():
    os.chdir("/www")
    server_address = ('', 80)
    handler = http.server.SimpleHTTPRequestHandler
    httpd = http.server.HTTPServer(server_address, handler)
    httpd.serve_forever()

def setup_http():
    os.makedirs("/www", exist_ok=True)
    run(f"ln -s {iso_mount_path} /www")
    shutil.copy(f"manifests/pxeboot/kickstart.ks", "/www/")

    p = Process(target=http_server)
    p.start()
    children.append(p)

def setup_tftp():
    print("Configuring TFTP")
    run("sed -i 's|^ExecStart=/usr/sbin/in.tftpd|ExecStart=/usr/sbin/in.tftpd -B 1468|' /usr/lib/systemd/system/tftp.service")
    os.makedirs("/var/lib/tftpboot/pxelinux", exist_ok=True)
    print("starting in.tftpd")
    run("killall in.tftpd")
    p = run("/usr/sbin/in.tftpd -s -L /var/lib/tftpboot")
    children.append(p)
    shutil.copy(f"{iso_mount_path}/images/pxeboot/vmlinuz", "/var/lib/tftpboot/pxelinux")
    shutil.copy(f"{iso_mount_path}/images/pxeboot/initrd.img", "/var/lib/tftpboot/pxelinux")
    shutil.copy(f"{iso_mount_path}/EFI/BOOT/grubaa64.efi", "/var/lib/tftpboot/")
    os.chmod("/var/lib/tftpboot/grubaa64.efi", 0o744)
    shutil.copy(f"manifests/pxeboot/grub.cfg", "/var/lib/tftpboot/grub.cfg")

def setup_dhcp():
    print("Configuring DHCP")
    #TODO add dev name from args
    run("ip addr add 172.131.100.1/24 dev eno4")
    shutil.copy(f"manifests/pxeboot/dhcpd.conf", "/etc/dhcp/dhcpd.conf")
    run("killall dhcpd")
    p = run("/usr/sbin/dhcpd -cf /etc/dhcp/dhcpd.conf -user dhcpd -group dhcpd")
    children.append(p)

def mount_iso():
    #TODO get iso into container from argument
    os.makedirs(iso_mount_path, exist_ok=True)
    run(f"umount {iso_mount_path}")
    run(f"mount -t iso9660 -o loop {iso_path} {iso_mount_path}")

def prepare_pxeboot():
    setup_dhcp()
    mount_iso()
    setup_tftp()
    setup_http()

def try_pxeboot():
    print("Preparing services for Pxeboot")
    prepare_pxeboot()
    print("Giving services time to settle")
    time.sleep(10)
    uefi_pxe_boot()
    print("Terminating http, tftp, and dhcpd")
    for e in children:
        try:
            e.terminate()
        except:
            pass
    run("pkill dhcpd")
    run("pkill in.tftpd")
    


def kill_existing():
    pids = [pid for pid in os.listdir('/proc') if pid.isdigit()]

    own_pid = os.getpid()
    for pid in filter(lambda x: int(x) != own_pid, pids):
        try:
            with open(os.path.join('/proc', pid, 'cmdline'), 'rb') as f:
                # print(f.read().decode("utf-8"))
                zb = b'\x00'
                cmd = [x.decode("utf-8") for x in f.read().strip(zb).split(zb)]
                if ("python" in cmd[0] and os.path.basename(cmd[1]) == 'pxeboot.py'):
                    print(f"Killing pid {pid}")
                    os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass


def main():
    kill_existing()
    try_pxeboot()

if __name__ == "__main__":
    main()