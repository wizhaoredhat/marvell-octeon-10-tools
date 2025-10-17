# marvell-octeon-10-tools
Marvell Octeon 10 Tools

```bash
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" <cmd>
```

## PXE Booting RHEL on Marvell DPU

See [pxe_boot_rhel.md](pxe_boot_rhel.md).

## Tools

### Reset

Utilize the serial interface at /dev/ttyUSB1 to trigger a reset of the associated Marvell DPU

Usage:
```bash
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
```bash
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" \
  ./pxeboot.py --help
```

### Host-setup

From the host with the DPU, we call commands like `pxeboot.py` or `fwupdate.py`.

We usually also want to setup that host in a way that is convenient for accessing the host.

Run
```bash
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" host-setup
```

This runs `pxeboot.py -H` and installs the ssh-key on the DPU (via `ssh-trust-dpu` script).

### FW Updater

Utilize the serial interface at /dev/ttyUSB0 to update the card with the provided firmware

Usage:
```bash
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it "$IMAGE" \
  ./fwupdate.py --dev eno4 /host/root/flash-uefi-cn10ka-11.24.02.img
```


### Pre-requisites
- Ensure dhcpd, and tftpf are not actively running on the host, as these services will be handled automatically from the container

```bash
killall dhcpd
killall in.tftpd
systemctl stop tftp.service
systemctl stop tftp.socket
```

### Run octep_cp_agent

On aarch64/arm64, the container also contains a build of octep_cp_agent from [github](https://github.com/MarvellEmbeddedProcessors/pcie_ep_octeon_target.git).
See `/usr/bin/{octep_cp_agent,cn106xx.cfg}`. You can run:

```bash
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

### Add Marvell DPU as Openshift Node running Red Hat CoreOS

#### Preparation

1) Ensure proper [Ethernet Port Setup](docs/howto_ethernet.md). You will want
to have at least two secondary interfaces on the DPU, one to use for the OCP
network (192.168.122.0/24?) and one for external.
\
In our original two-cluster setup, we only had the slow(er) primary (RJ45) interface
and one secondary. That is almost too limited. You would need to come up with some
elaborate network configuration (e.g. use the primary interface for the OCP network
or maybe some VLANs on the secondary?). Just configure more secondary ports.

2) Potential problem: the MAC address on the DPU may be random. That causes problems
because RHCOS wants to boot with `ip=$MAC:dhcp` on the command line. If the MAC address
is wrong, it cannot boot. Workaround: enter the grub menu and edit to `ip:dhcp`. Proper
solution is to [configure a fixed MAC address](docs/howto_fix_mac_addresses.txt).

#### Add Node

1) Assume we have Assisted Installer running and the cluster already created. Find the cluster ID with
```bash
aicli -u 0.0.0.0:8090 list clusters

AI_URL="http://127.0.0.1:8090"
CLUSTER_ID=$(curl -s -X GET "$AI_URL/api/assisted-install/v2/clusters?with_hosts=true" -H "accept: application/json" -H "get_unregistered_clusters: false"| jq -r '.[].id')
echo "$CLUSTER_ID"
```

2) Create new infraenv to get the download URL for the discovery ISO via
```bash
aicli -u 0.0.0.0:8090 list infraenvs

OUT="$(
    curl -X POST "$AI_URL/api/assisted-install/v2/infra-envs" -H "Content-Type: application/json" -d '{
        "name": "ocpcluster-arm64",
        "cluster_id": "'"$CLUSTER_ID"'",
        "cpu_architecture": "arm64",
        "pull_secret": "'"$(cat ~/pull_secret.json | sed 's/"/\\"/g')"'",
        "ssh_authorized_key": "'"$(cat ~/.ssh/id_rsa.pub)"'"
      }'
    )"

echo "$OUT" | jq
DOWNLOAD_URL="$(jq -r '.download_url' <<< "$OUT")"
INFRAENV_ID="$(jq -r '.id' <<< "$OUT")"
printf 'DOWNLOAD_URL=%q\n' "$DOWNLOAD_URL"
printf 'INFRAENV_ID=%q\n' "$INFRAENV_ID"

aicli -u 0.0.0.0:8090 info infraenv "$INFRAENV_ID"
```
Alternatively, you can modify an existing infraenv with
```bash
curl -X PATCH "$AI_URL/api/assisted-install/v2/infra-envs/$INFRAENV_ID" -H "Content-Type: application/json" -d '{
    "ssh_authorized_key": "'"$(cat ~/.ssh/id_rsa.pub)"'"
  }'

aicli -u 0.0.0.0:8090 info infraenv "$INFRAENV_ID"
```

3) Run pxeboot command on Marvell Host. Usually, we run this command on the
DPU's host and use an interface connected to the primary network. That way, the
tool has access to `/dev/ttyUSB0` and can automatically reboot.
\
You may also be able to run the pxeboot command on another host on a connected
network. For example, using the `--prompt` option, to only start the DHCP, TFTP
and HTTP servers. See also the `--dpu-dev` option to PXE boot from a secondary
interface.
```bash
IMAGE=quay.io/sdaniele/marvell-tools:latest
sudo podman run --pull always --rm --replace --privileged --pid host --network host --user 0 --name marvell-tools -v /:/host -v /dev:/dev -it \
    "$IMAGE" \
    ./pxeboot.py \
    "$DOWNLOAD_URL" \
    --ssh-key '' \
    --ssh-key "$(ls -1 ~/.ssh/id*pub | xargs -n1 cat)"
```

4) Wait, then you should see the new host in
```bash
aicli -u 0.0.0.0:8090 list hosts

HOST_ID="$(aicli -u 0.0.0.0:8090 list host | grep " ocpcluster-arm64 " | awk '{ print $4 }')"
printf 'HOST_ID=%q\n' "$HOST_ID"
```

5) Check status via
```bash
aicli -u 0.0.0.0:8090 info host "$HOST_ID"
```
Look out for problems. In particular connectivity problems. The DPU must reach
the Assisted Installer IP address and also the OCP clusters network (in our
setups usually 192.168.122.0/24). Check preparation steps above.

6) Set Hostname for new node:
```bash
NEW_HOSTNAME=...

aicli -u 0.0.0.0:8090 update host "$HOST_ID" -P requested_hostname="$NEW_HOSTNAME"

HOST_NAME="$(aicli -u 0.0.0.0:8090 list host | grep "$HOST_ID" | awk '{ print $2 }')"
printf 'HOST_NAME=%q\n' "$HOST_NAME"
```

7) Configure hugepages
```bash
cat << EOF | oc apply -f -
apiVersion: machineconfiguration.openshift.io/v1
kind: MachineConfigPool
metadata:
  name: dpu-specific-marvell
spec:
  nodeSelector:
    matchExpressions:
      - key: dpu.config.openshift.io/dpuside
        operator: In
        values:
          - dpu
      - key: kubernetes.io/hostname
        operator: In
        values:
          - "$HOST_NAME"
  machineConfigSelector:
    matchExpressions:
      - key: machineconfiguration.openshift.io/role
        operator: In
        values:
          - worker
          - dpu-specific-marvell
  maxUnavailable: 1
EOF

cat << EOF | oc apply -f -
apiVersion: machineconfiguration.openshift.io/v1
kind: MachineConfig
metadata:
  name: 99-dpu-marvell-kargs-hugepages
  labels:
    machineconfiguration.openshift.io/role: dpu-specific-marvell
spec:
  kernelArguments:
    - default_hugepagesz=32M
    - hugepagesz=32M
    - hugepages=32
  config:
    ignition:
      version: 3.4.0
EOF
```

8) Start installation of host
```bash
aicli -u 0.0.0.0:8090 start host "$HOST_ID"
```

9) Approve the Certificate Signing Request (CSR) for the new node.
```bash
oc get csr

oc get csr -o name | xargs oc adm certificate approve
```
Double check after a while that no further CSR are waiting approval.

10) Check whether we are ready
```bash
oc get node
```

#### DPU Operator

1) deploy operator

2) create DpuOperatorConfig, for example `examples/config.yaml` from the
operator's git repository.

3) label host and DPU side with `dpu=true`
