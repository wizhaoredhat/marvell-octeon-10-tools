import argparse
import os
import signal
from multiprocessing import Process
import pexpect
import time
import shutil
import http.server
from reset import reset
from common_dpu import run, minicom_cmd


children = []


def parse_args():
    parser = argparse.ArgumentParser(description="Process FW IMG file.")
    parser.add_argument("img", type=str, help="Mandatory argument of type string for IMG file (make sure the path to this file was mounted when running the pod via -v /host/img:/container/img).")
    parser.add_argument("--dev", type=str, default="eno4", help="Optional argument of type string for device. Default is 'eno4'.")

    args = parser.parse_args()
    if not os.path.exists(args.img):
        print(f"Couldn't read img file {args.img}")
        raise Exception("Invalid path to omg provided")

    return args

def run_process(cmd):
    p = Process(target=run, args=(cmd,))
    p.start()
    return p


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


def firmware_update():
    print("firmware updating")
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
    print("waiting for instructions to Boot from Primary Boot Device")
    child.expect("1\) Boot from Primary Boot Device", 10)
    time.sleep(1)
    child.send('1')
    print("waiting to escape to uboot menu")
    child.expect("Hit any key to stop autoboot", 60)
    print("Sending escape 5 times")
    child.send(ESC*5)
    print("waiting on uboot prompt")
    child.expect("crb106-pcie>", 5)
    print("Enabling 100G management port")
    child.send("setenv ethact rvu_pf#1")
    child.send(KEY_ENTER)
    child.send("saveenv")
    child.send(KEY_ENTER)
    child.expect("OK", 10)
    child.send("setenv serverip 172.131.100.1")
    child.send(KEY_ENTER)
    child.send("tftpboot $loadaddr flash-uefi-cn10ka.img")
    child.send(KEY_ENTER)
    # set to secondary SPI flash
    child.send("sf probe 1:0")
    child.send(KEY_ENTER)
    child.send("sf update $fileaddr 0 $filesize")
    child.send(KEY_ENTER)
    child.send("reset")
    child.send(KEY_ENTER)
    child.close()
    print("Closing minicom")

def uboot_firmware_update():
    print("Starting FW Update")
    print("Resetting card")
    reset()
    firmware_update()

def setup_tftp(img):
    print("Configuring TFTP")
    os.makedirs("/var/lib/tftpboot", exist_ok=True)
    print("starting in.tftpd")
    run("killall in.tftpd")
    p = run_process("/usr/sbin/in.tftpd -s -B 1468 -L /var/lib/tftpboot")
    children.append(p)
    shutil.copy(f"{img}", "/var/lib/tftpboot")

def setup_dhcp(dev: str):
    print("Configuring DHCP")
    run(f"ip addr add 172.131.100.1/24 dev {dev}")
    shutil.copy(f"manifests/pxeboot/dhcpd.conf", "/etc/dhcp/dhcpd.conf")
    run("killall dhcpd")
    p = run_process("/usr/sbin/dhcpd -f -cf /etc/dhcp/dhcpd.conf -user dhcpd -group dhcpd")
    children.append(p)

def prepare_fwupdate(args):
    setup_dhcp(args.dev)
    setup_tftp(args.img)

def try_fwupdate(args):
    print("Preparing services for FW update")
    prepare_fwupdate(args)
    print("Giving services time to settle")
    time.sleep(10)
    uboot_firmware_update()
    print("Terminating http, tftp, and dhcpd")
    for e in children:
        e.terminate()

def kill_existing():
    pids = [pid for pid in os.listdir('/proc') if pid.isdigit()]

    own_pid = os.getpid()
    for pid in filter(lambda x: int(x) != own_pid, pids):
        try:
            with open(os.path.join('/proc', pid, 'cmdline'), 'rb') as f:
                # print(f.read().decode("utf-8"))
                zb = b'\x00'
                cmd = [x.decode("utf-8") for x in f.read().strip(zb).split(zb)]
                if ("python" in cmd[0] and os.path.basename(cmd[1]) == 'fwupdate.py'):
                    print(f"Killing pid {pid}")
                    os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass


def main():
    args = parse_args()
    kill_existing()
    try_fwupdate(args)

if __name__ == "__main__":
    main()