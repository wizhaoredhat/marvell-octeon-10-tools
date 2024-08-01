FROM quay.io/centos/centos:stream9

RUN dnf install -y 'dnf-command(config-manager)' && \
    dnf config-manager --set-enabled crb && \
    dnf install -y epel-next-release epel-release && \
    dnf install \
        dhcp-server \
        iproute \
        iputils \
        minicom \
        procps \
        python3-pip \
        python39 \
        tftp \
        tftp-server \
        tini \
        -y && \
    ln -s /usr/bin/python3.9 /usr/bin/python && \
    pip3.9 install \
        requests \
        pexpect

COPY * /
COPY manifests /manifests

RUN mv /manifests/.minirc.dfl /root/

ENTRYPOINT ["/usr/bin/tini", "-s", "-p", "SIGTERM", "-g", "-e", "143", "--"]
CMD ["/usr/bin/sleep", "infinity"]
