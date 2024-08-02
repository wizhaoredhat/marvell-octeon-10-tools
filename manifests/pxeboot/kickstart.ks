rootpw redhat

# System language
lang en_US.UTF-8

# System timezone
timezone America/New_York --utc

# Use text mode install
text

# Accept the license
eula --agreed

# Disable firewall
firewall --disabled

# Do not configure the X Window System
skipx

# Disable the Setup Agent on first boot
firstboot --disabled

# Network information
network --bootproto=dhcp --hostname=marvell-dpu.redhat --device=enP2p6s0 --activate

ignoredisk --only-use=nvme0n1
# System bootloader configuration
bootloader --append="crashkernel=1G-4G:256M,4G-64G:320M,64G-:576M" --location=mbr --boot-drive=nvme0n1
autopart
# Partition clearing information
clearpart --all --initlabel --drives=nvme0n1

# Reboot after installation
reboot

%packages --ignoremissing
@base
@core
@Development Tools
python3-devel
atk
cairo
tcl
tk
nfs-utils
chrony
dhclient
vim
ethtool
git
grubby
xterm
NetworkManager-config-server
%end

################################################################################
#
################################################################################

%post --log=/var/log/kickstart_post.log

set -x

SSH_PUBKEY=@__SSH_PUBKEY__@
if [ -n "$SSH_PUBKEY" ] ; then
    mkdir -p /root/.ssh
    touch /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    printf '%s\n' "$SSH_PUBKEY" >> /root/.ssh/authorized_keys
fi

################################################################################

cat <<EOF > /etc/NetworkManager/system-connections/enP2p2s0-dpu-secondary.nmconnection
[connection]
id=enP2p2s0-dpu-secondary
uuid=$(uuidgen)
type=ethernet
autoconnect-priority=20
interface-name=enP2p2s0

[ipv4]
method=auto
dhcp-timeout=2147483647
route-metric=110

[ipv6]
method=auto
addr-gen-mode=eui64
route-metric=110
EOF
chmod 600 /etc/NetworkManager/system-connections/enP2p2s0-dpu-secondary.nmconnection

cat <<EOF > /etc/NetworkManager/system-connections/enP2p3s0-dpu-host.nmconnection
[connection]
id=enP2p3s0-dpu-host
uuid=$(uuidgen)
type=ethernet
autoconnect-priority=10
interface-name=enP2p3s0

[ipv4]
method=auto
address1=@__DPU_IP4ADDRNET__@,@__HOST_IP4ADDR__@
dhcp-timeout=2147483647
route-metric=120

[ipv6]
method=auto
addr-gen-mode=eui64
route-metric=120
EOF
chmod 600 /etc/NetworkManager/system-connections/enP2p3s0-dpu-host.nmconnection

%end
