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

################################################################################

sed -i 's/^GRUB_CMDLINE_LINUX="\(.*\)"$/GRUB_CMDLINE_LINUX="\1 default_hugepagesz=32M hugepagesz=32M hugepages=32"/' /etc/default/grub
grub2-mkconfig -o /boot/grub2/grub.cfg

################################################################################

SSH_PUBKEY=@__SSH_PUBKEY__@
if [ -n "$SSH_PUBKEY" ] ; then
    mkdir -p /root/.ssh
    touch /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    printf '%s\n' "$SSH_PUBKEY" >> /root/.ssh/authorized_keys
fi

################################################################################

cat <<EOF > /etc/NetworkManager/system-connections/dpu-host.nmconnection
@__NM_PROFILE_NM_HOST__@
EOF
chmod 600 /etc/NetworkManager/system-connections/dpu-host.nmconnection

cat <<EOF > /etc/NetworkManager/system-connections/dpu-secondary.nmconnection
@__NM_PROFILE_NM_SECONDARY__@
EOF
chmod 600 /etc/NetworkManager/system-connections/dpu-secondary.nmconnection

################################################################################

cat <<EOF > /etc/NetworkManager/conf.d/89-marvell-tools-unmanaged-devices.conf
@__NM_CONF_UNMANAGED_DEVICES__@
EOF

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
    URL_PART=$(curl -L -s "$URL_BASE" | sed -n 's/.*href="\(RHEL-'"$OS_VERSION"'.0-updates[^"]*\)".*/\1/p' | grep -v delete-me/ | sort | tail -n1)
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

_dnf_install_urls() {
    if [ "$#" -gt 0 ] ; then
        (
            local tmp_files=()
            local packages=()
            local pkg
            local tmp

            trap 'rm -f "${tmp_files[@]}"' EXIT

            for pkg ; do
                if [[ "$pkg" =~ ^untrusted:https?:// ]]; then
                    # We want to ignore TLS errors. Hence download the file first.
                    tmp="$(mktemp -t dnf-install-pkg-XXXXXX.rpm)"
                    tmp_files+=( "$tmp" )
                    curl -L -k -o "$tmp" "${pkg#untrusted:}"
                    pkg="$tmp"
                fi
                packages+=( "$pkg" )
            done

            dnf install -y "${packages[@]}"
        )
    fi
}

EXTRA_PACKAGES=( @__EXTRA_PACKAGES__@ )
if [ "@__DEFAULT_EXTRA_PACKAGES__@" = 1 ] ; then
    case "$(sed -n 's/^VERSION_ID="\(.*\)"/\1/p' /etc/os-release)" in
        9.6)
            EXTRA_PACKAGES+=(
                # Nothing for now.
            )
            ;;
        *)
            ;;
    esac
fi
_dnf_install_urls "${EXTRA_PACKAGES[@]}"

################################################################################

cat <<'EOF' > /usr/bin/dpu-monitor.sh
#!/bin/bash

ip -d link || :
ip -ts monitor link addr route
EOF

chmod +x /usr/bin/dpu-monitor.sh

cat <<'EOF' > /etc/systemd/system/dpu-monitor.service
[Unit]
Description=Monitor DPU for Debugging
Before=octep_cp_agent.service
Before=ip-link-up.service
Before=pre-network.target

[Service]
ExecStart=/usr/bin/dpu-monitor.sh

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable dpu-monitor.service

################################################################################

cat <<'EOF' > /usr/bin/ip-link-up.sh
#!/bin/bash

set -ex

# Seems that we must ensure that all SDP interfaces are always up (and up
# before anything else). See https://issues.redhat.com/browse/RHEL-90248
for f in $(cd /sys/class/net/ && ls -1d enP2p1s0* 2>/dev/null) ; do
    ip link set "$f" up || :
done
EOF

chmod +x /usr/bin/ip-link-up.sh

cat <<'EOF' > /etc/systemd/system/ip-link-up.service
[Unit]
Description=Set up SDP interfaces (RHEL-902048)
After=local-fs.target
Before=pre-network.target
Wants=pre-network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/ip-link-up.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl disable ip-link-up.service

################################################################################

cat <<'EOF' > /usr/bin/run_octep_cp_agent
#!/bin/bash

CMD="${1:-run}"
IMAGE="${IMAGE:-quay.io/sdaniele/marvell-tools:latest}"
IMAGE="${2:-$IMAGE}"
NAME=marvell-tools-cp-agent

case "$CMD" in
    run)
        podman run --pull newer --rm --replace --privileged --pid host --network host --user 0 --name "$NAME" -v /:/host -v /dev:/dev -it "$IMAGE" exec_octep_cp_agent
        ;;
    stop)
        podman stop "$NAME"
        ;;
    *)
        echo "Usage: run_octep_cp_agent [ run | stop ] [ IMAGE ]"
        echo "IMAGE=\"$IMAGE\""
        echo "NAME=\"$NAME\""
        podman ps -a --filter "name=$NAME"
        exit 1
        ;;
esac
EOF

chmod +x /usr/bin/run_octep_cp_agent

cat <<'EOF' > /etc/systemd/system/octep_cp_agent.service
[Unit]
Description=Run octep_cp_agent
After=network-online.target

[Service]
ExecStart=/usr/bin/run_octep_cp_agent run
ExecStop=/usr/bin/run_octep_cp_agent stop
Environment="IMAGE=quay.io/sdaniele/marvell-tools:latest"

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
if [ "@__OCTEP_CP_AGENT_SERVICE_ENABLE__@" = 1 ] ; then
  systemctl enable octep_cp_agent.service
fi

################################################################################

dnf remove -y nano

################################################################################

mkdir -p /var/log/journal

################################################################################

# Allow password login as root.
sed -i 's/.*PermitRootLogin.*/# \0\nPermitRootLogin yes/' /etc/ssh/sshd_config

systemctl disable NetworkManager-wait-online.service

echo 'export KUBECONFIG=/var/lib/microshift/resources/kubeadmin/kubeconfig' > /etc/profile.d/marvell-tools-kubeconfig.sh

%end
