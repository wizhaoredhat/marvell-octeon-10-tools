FROM quay.io/centos/centos:stream9 AS builder-octep-cp-agent
ARG TARGETPLATFORM

WORKDIR /build/

RUN if [ "$TARGETPLATFORM" = "linux/arm64" ] ; then \
        set -x && \
        \
        dnf config-manager --set-enabled crb && \
        dnf upgrade -y && \
        dnf install -y \
            g++ \
            gcc \
            git-core \
            libconfig-devel && \
        \
        git clone https://github.com/MarvellEmbeddedProcessors/pcie_ep_octeon_target.git && \
        \
        cd /build/pcie_ep_octeon_target/ && \
        git checkout -B tmp aa84a2331f76b68583e7b5861f17f5f3cef0fbd0 && \
        \
        cd /build/pcie_ep_octeon_target/target/libs/octep_cp_lib/ && \
        make CFLAGS="-DUSE_PEM_AND_DPI_PF=1" && \
        \
        cd /build/pcie_ep_octeon_target/target/apps/octep_cp_agent/ && \
        make CFLAGS="$(pkg-config --cflags libconfig) -I/build/pcie_ep_octeon_target/target/libs/octep_cp_lib/bin/include" \
             LDFLAGS="$(pkg-config --libs libconfig) -L/build/pcie_ep_octeon_target/target/libs/octep_cp_lib/bin/lib" && \
        \
        cp /build/pcie_ep_octeon_target/target/apps/octep_cp_agent/bin/bin/octep_cp_agent /build/octep_cp_agent.25.03.0 && \

        cd /build/pcie_ep_octeon_target/ && \
        git clean -fdx && \
        git checkout -B tmp 35c9be07d2eefe1c909efefc9faa495db965a58e && \
        \
        cd /build/pcie_ep_octeon_target/target/libs/octep_cp_lib/ && \
        make CFLAGS="-DUSE_PEM_AND_DPI_PF=1" && \
        \
        cd /build/pcie_ep_octeon_target/target/apps/octep_cp_agent/ && \
        make CFLAGS="$(pkg-config --cflags libconfig) -I/build/pcie_ep_octeon_target/target/libs/octep_cp_lib/bin/include" \
             LDFLAGS="$(pkg-config --libs libconfig) -L/build/pcie_ep_octeon_target/target/libs/octep_cp_lib/bin/lib" ; \
    fi

###############################################################################

FROM quay.io/centos/centos:stream9
ARG TARGETPLATFORM

RUN dnf install -y 'dnf-command(config-manager)' && \
    dnf config-manager --set-enabled crb && \
    dnf install -y epel-next-release epel-release && \
    dnf upgrade -y --skip-broken --allowerasing && \
    dnf install \
        /usr/bin/ssh-keygen \
        dhcp-server \
        ethtool \
        git-core \
        iproute \
        iputils \
        libconfig \
        minicom \
        nftables \
        pciutils \
        procps \
        python-unversioned-command \
        python3-pip \
        python3-pyserial \
        python3-pyyaml \
        python3-requests \
        python3-types-pyyaml \
        python39 \
        sshpass \
        tcpdump \
        tftp \
        tftp-server \
        tini \
        unzip \
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

COPY manifests/ssh-trust-dpu /usr/bin/
COPY manifests/host-setup /usr/bin/

COPY --from=builder-octep-cp-agent /build/ /build/
COPY manifests/exec_octep_cp_agent /build/

RUN if [ "$TARGETPLATFORM" = "linux/arm64" ] ; then \
        mv /build/pcie_ep_octeon_target/target/apps/octep_cp_agent/bin/bin/octep_cp_agent \
           /build/pcie_ep_octeon_target/target/apps/octep_cp_agent/cn106xx.cfg \
           /build/octep_cp_agent.25.03.0 \
           /usr/bin/ && \
        mv /build/exec_octep_cp_agent /usr/bin/ && \
        ldconfig ; \
    fi && \
    rm -rf /build/

ENV PATH="/marvell-octeon-10-tools:$PATH"
WORKDIR /marvell-octeon-10-tools
ENTRYPOINT ["/usr/bin/tini", "-s", "-p", "SIGTERM", "-g", "-e", "143", "--"]
CMD ["/usr/bin/sleep", "infinity"]
