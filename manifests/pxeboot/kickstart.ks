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
network --bootproto=dhcp --hostname=@__HOSTNAME__@ --device=enP2p6s0 --activate

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
podman
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

[ethernet]
cloned-mac-address=@__NM_SECONDARY_CLONED_MAC_ADDRESS__@

[ipv4]
method=auto
dhcp-timeout=2147483647
route-metric=110
@__NM_SECONDARY_IP_ADDRESS__@

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

################################################################################

cat <<EOF >> /etc/chrony.conf

# Appended by marvell-tools
@__CHRONY_SERVERS__@
EOF

################################################################################

cat <<'EOF_MARVELL_TOOLS_BEAKER' > /etc/yum.repos.d/marvell-tools-beaker.sh
#!/bin/bash

set -xe

URL="$1"
ENABLED="${2-1}"

if [ -z "$URL" ] ; then
    OS_VERSION="$(sed -n 's/^VERSION_ID="\(.*\)"$/\1/p' /etc/os-release | head -n1)"
    URL_BASE='http://download.hosts.prod.upshift.rdu2.redhat.com/rhel-9/composes/RHEL-9/'
    URL_PART=$(curl -s "$URL_BASE" | sed -n 's/.*href="\(RHEL-'"$OS_VERSION"'.0-updates[^"]*\)".*/\1/p' | grep -v delete-me/ | sort | tail -n1)
    if [ -z "$URL_PART" -o -z "$OS_VERSION" ] ; then
        exit 1
    fi
    URL="$URL_BASE$URL_PART"
fi

beaker_repo() {
    local name="$1"
cat <<EOF
[beaker-$name]
name=beaker-$name
baseurl=${URL}compose/$name/aarch64/os
enabled=${ENABLED}
gpgcheck=0
skip_if_unavailable=1
priority=200

EOF
}

(
    beaker_repo BaseOS
    beaker_repo AppStream
) \
    > /etc/yum.repos.d/marvell-tools-beaker.repo

EOF_MARVELL_TOOLS_BEAKER

chmod +x /etc/yum.repos.d/marvell-tools-beaker.sh

/etc/yum.repos.d/marvell-tools-beaker.sh @__YUM_REPO_URL__@ @__YUM_REPO_ENABLED__@

EXTRA_PACKAGES=( @__EXTRA_PACKAGES__@ )
if [ "@__DEFAULT_EXTRA_PACKAGES__@" = 1 ] ; then
    case "$(sed -n 's/^VERSION_ID="\(.*\)"/\1/p' /etc/os-release)" in
        *)
            ;;
    esac
fi
if [ "${#EXTRA_PACKAGES[@]}" -gt 0 ] ; then
    dnf install -y "${EXTRA_PACKAGES[@]}"
fi

################################################################################

# Allow password login as root.
sed -i 's/.*PermitRootLogin.*/# \0\nPermitRootLogin yes/' /etc/ssh/sshd_config

%end
