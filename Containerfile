FROM quay.io/centos/centos:stream9

RUN dnf install -y 'dnf-command(config-manager)' && \
    dnf config-manager --set-enabled crb && \
    dnf install -y epel-next-release epel-release && \
    dnf upgrade -y --skip-broken --allowerasing && \
    dnf install \
        dhcp-server \
        ethtool \
        iproute \
        iputils \
        minicom \
        procps \
        python-unversioned-command \
        python3-pexpect \
        python3-pip \
        python3-requests \
        python39 \
        tftp \
        tftp-server \
        tini \
        vim \
        -y && \
    echo "export PYTHONPATH=/marvell-octeon-10-tools" > /etc/profile.d/marvell-octeon-10-tools.sh

COPY ./*.py ./*sh /marvell-octeon-10-tools/
COPY ./manifests /marvell-octeon-10-tools/manifests

COPY manifests/.minirc.dfl /root/

WORKDIR /marvell-octeon-10-tools
ENTRYPOINT ["/usr/bin/tini", "-s", "-p", "SIGTERM", "-g", "-e", "143", "--"]
CMD ["/usr/bin/sleep", "infinity"]
