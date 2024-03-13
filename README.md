# marvell-octeon-10-tools
Marvell Octeon 10 Tools

```
sudo podman run --pull always --replace --pid host --network host --user 0 --name marvell-tools -dit --privileged -v /dev:/dev quay.io/sdaniele/marvell-tools
sudo podman exec -it marvell-tools <cmd>
```

## Tools

### Reset

Utilize the serial interface at /dev/ttyUSB1 to trigger a reset of the associated Marvell DPU

Usage:
```
python reset.py
```

### PxeBoot

Utilize the serial interface at /dev/ttyUSB0 to pxeboot the card with the provided ISO

Usage:
```
sudo podman run --pull always --replace --pid host --network host --user 0 --name marvell-tools -dit --privileged -v /dev:/dev -v /<path_to_iso>/iso:/tmp/iso quay.io/wizhao/marvell-tools
sudo podman exec -it marvell-tools /bin/bash
python pxeboot.py --dev eno4 /tmp/iso
```

Pre-requisites:
- Ensure dhcpd, and tftpf are not actively running on the host, as these services will be handled automatically from the container

```
killall dhcpd
killall in.tftpd
systemctl stop tftp.service
systemctl stop tftp.socket
```
