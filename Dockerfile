FROM quay.io/centos/centos:stream9

RUN dnf install -y 'dnf-command(config-manager)' && \
    dnf config-manager --set-enabled crb && \
    dnf install -y epel-next-release epel-release && \
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
        -y

COPY * /
COPY manifests /manifests

RUN mv /manifests/.minirc.dfl /root/

ENTRYPOINT ["/usr/bin/tini", "-s", "-p", "SIGTERM", "-g", "-e", "143", "--"]
CMD ["/usr/bin/sleep", "infinity"]
