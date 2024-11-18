FROM quay.io/centos/centos:stream9

RUN dnf install -y 'dnf-command(config-manager)' && \
    dnf config-manager --set-enabled crb && \
    dnf install -y epel-next-release epel-release && \
    dnf upgrade -y --skip-broken --allowerasing && \
    dnf install \
        /usr/bin/ssh-keygen \
        dhcp-server \
        ethtool \
        git \
        iproute \
        iputils \
        minicom \
        nftables \
        procps \
        python-unversioned-command \
        python3-pip \
        python3-pyserial \
        python3-pyyaml \
        python3-requests \
        python3-types-pyyaml \
        python39 \
        tftp \
        tftp-server \
        tini \
        vim \
        -y && \
    echo "export PYTHONPATH=/marvell-octeon-10-tools" > /etc/profile.d/marvell-octeon-10-tools.sh

COPY requirements.txt /tmp/
RUN python3 -m pip install -r /tmp/requirements.txt && \
    rm -rf /tmp/requirements.txt

COPY ./*.py ./*sh ./mypy.ini /marvell-octeon-10-tools/
COPY ./manifests /marvell-octeon-10-tools/manifests

COPY manifests/.minirc.dfl /root/
COPY manifests/Minicom /usr/bin/

WORKDIR /marvell-octeon-10-tools
ENTRYPOINT ["/usr/bin/tini", "-s", "-p", "SIGTERM", "-g", "-e", "143", "--"]
CMD ["/usr/bin/sleep", "infinity"]
