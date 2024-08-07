# marvell-octeon-10-tools
Marvell Octeon 10 Tools

```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --replace --pid host --network host --user 0 --name marvell-tools -dit --privileged -v /dev:/dev "$IMAGE"
sudo podman exec -it marvell-tools <cmd>
```

## Tools

### Reset

Utilize the serial interface at /dev/ttyUSB1 to trigger a reset of the associated Marvell DPU

Usage:
```
python /marvell-octeon-10-tools/reset.py
```

### PxeBoot

Utilize the serial interface at /dev/ttyUSB0 to pxeboot the card with the provided ISO

Usage:
```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -dit "$IMAGE"
sudo podman exec -it marvell-tools python /marvell-octeon-10-tools/pxeboot.py --dev eno4 /host/root/RHEL-9.4.0-20240312.96-aarch64-dvd1.iso
```

### FW Updater

Utilize the serial interface at /dev/ttyUSB0 to update the card with the provided firmware

Usage:
```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --replace --pid host --network host --user 0 --name marvell-tools -dit --privileged -v /dev:/dev -v /root/flash-uefi-cn10ka-11.24.02.img:/tmp/img "$IMAGE"
sudo podman exec -it marvell-tools /bin/bash
python /marvell-octeon-10-tools/fwupdate.py --dev eno4 /tmp/img
```


### Pre-requisites
- Ensure dhcpd, and tftpf are not actively running on the host, as these services will be handled automatically from the container

```
killall dhcpd
killall in.tftpd
systemctl stop tftp.service
systemctl stop tftp.socket
```
