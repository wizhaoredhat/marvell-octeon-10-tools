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

%end
