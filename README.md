# marvell-octeon-10-tools
Marvell Octeon 10 Tools

```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" <cmd>
```

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

Usage:
```
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" \
  ./pxeboot.py --help
```

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
