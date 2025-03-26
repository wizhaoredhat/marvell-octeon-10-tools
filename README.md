# marvell-octeon-10-tools
Marvell Octeon 10 Tools

```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" <cmd>
```

## PXE Booting RHEL on Marvell DPU

See [pxe_boot_rhel.md](pxe_boot_rhel.md).

## Tools

### Reset

Utilize the serial interface at /dev/ttyUSB1 to trigger a reset of the associated Marvell DPU

Usage:
```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" \
  ./reset.py
```

### PxeBoot

Utilize the serial interface at /dev/ttyUSB0 to pxeboot the card with the provided ISO

The tool makes several assumptions.

- The tool is for installing RHEL from an ISO via PXE. An HTTP url can be specified instead
  of an URL, in that case, it is downloaded to "/host/root/rhel-iso-*". You can also set
  "rhel:9.x" to use the latest RHEL 9.x nightly image.

- The DPU's serial interface is on /dev/ttyUSB0.

- The DPU's management interface (enP2p3s0) is connected back to the host (usually on "eno4", see "--dev" argument).
  On the DPU, a NetworkManager profile "enP2p3s0-dpu-host" will be created with static IP address 172.131.100.100/24
  (DHCP and SLAAC are enabled too).
  On the host, "eno4" should have address 172.131.100.1/24 and setup NAT for forwarding traffic. The tool
  takes care of that, see `nft list table ip marvell-tools-nat-eno4`.

- The DPU's secondary interface (enP2p2s0) is connected to the provisioning host, where we expect to run DHCP.
  On the DPU, a NetworkManager profile "enP2p3s0-dpu-secondary" will be created with DHCP and SLAAC enabled.
  If a DHCP server is running on the other end, it should also setup NAT to connect the DPU to the internet.

- Profile enP2p3s0-dpu-secondary has a better route metric than enP2p3s0-dpu-host and will be preferred to
  reach the internet. Both profiles attempt DHCP/SLAAC indefinitely.

- The host is expected to run RHEL or CoreOS. See also options "--host-setup-only" and "--host-mode".
  On the host:

  - It configures a NetworkManager connection profile "eno4-marvell-dpu" for "eno4" and activates it.
    This configuration is persisted.

  - Configures nftables (`nft list table ip marvell-tools-nat-eno4`) and "net.ipv4.ip_forward". This
    configuration is ephemeral. It can be redone after reboot with "--host-setup-only".

  - see also [Host-setup](#Host-setup).


Usage:
```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" \
  ./pxeboot.py --help
```

### Host-setup

From the host with the DPU, we call commands like `pxeboot.py` or `fwupdate.py`.

We usually also want to setup that host in a way that is convenient for accessing the host.

Run
```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" host-setup
```

This runs `pxeboot.py -H` and installs the ssh-key on the DPU (via `ssh-trust-dpu` script).

### FW Updater

Utilize the serial interface at /dev/ttyUSB0 to update the card with the provided firmware

Usage:
```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" \
  ./fwupdate.py --dev eno4 /host/root/flash-uefi-cn10ka-11.24.02.img
```


### Pre-requisites
- Ensure dhcpd, and tftpf are not actively running on the host, as these services will be handled automatically from the container

```
killall dhcpd
killall in.tftpd
systemctl stop tftp.service
systemctl stop tftp.socket
```

### Run octep_cp_agent

On aarch64/arm64, the container also contains a build of octep_cp_agent from [github](https://github.com/MarvellEmbeddedProcessors/pcie_ep_octeon_target.git).
See `/usr/bin/{octep_cp_agent,cn106xx.cfg}`. You can run:

```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools-cp-agent -v /:/host -v /dev:/dev -it "$IMAGE" \
  exec_octep_cp_agent
```

### Known Problems

- Various commands access the serial console at "/dev/ttyUSB[01]". As only one process
  at a time can access the serial console, make sure to not run other minicom processes
  in parallel.

- Sometimes pxeboot command hangs. The tool reboots the machine and expects the
  DPU to contact the DHCP/TFTP/HTTP services. Usually, 1-2 minutes after
  selecting the boot entry, we expect logging messages about "GET" requests. If
  that doesn't happen and you see lines about ping failures (that are normal
  while we wait for the DPU to be booted into the installed system), you
  encountered the problem. Abort the command and start again.

- Sometimes for pxeboot/fwupdate commands, dhcpd fails to start. The dhcpd
  process exits right away with a "Error getting hardware address for "vip": No
  such device" message and the boot fails (as no DHCP address is provided).
  In that case, `ip -d addr` also shows an address with label "vip".
  It's not clear to me who creates this address, but its label interferes with
  `ifconfig` and `dhcpd` programs.
  Run `$ ip addr del 192.168.122.101/32 dev br-ex scope global label vip ; ip addr add 192.168.122.101/32 dev br-ex scope global`
  and retry.
