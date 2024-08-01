FROM quay.io/centos/centos:stream9

RUN dnf install \
        dhcp-server \
        iproute \
        iputils \
        minicom \
        procps \
        python3-pip \
        python39 \
        tftp \
        tftp-server \
        -y && \
    ln -s /usr/bin/python3.9 /usr/bin/python && \
    pip3.9 install \
        requests \
        pexpect

COPY * /
COPY manifests /manifests

RUN mv /manifests/.minirc.dfl /root/
