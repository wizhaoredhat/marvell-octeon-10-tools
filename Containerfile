FROM quay.io/centos/centos:stream9 AS builder-octep-cp-agent
ARG TARGETPLATFORM

RUN if [ "$TARGETPLATFORM" = "linux/arm64" ] ; then \
        dnf upgrade -y && \
        dnf install -y \
            g++ \
            gcc \
            git-core ; \
    fi

WORKDIR /build/
RUN if [ "$TARGETPLATFORM" = "linux/arm64" ] ; then \
        curl -L -k -o /build/libconfig.tar.gz https://hyperrealm.github.io/libconfig/dist/libconfig-1.7.2.tar.gz && \
        tar -C /build -xvf libconfig.tar.gz && \
        git clone --depth=10 https://github.com/MarvellEmbeddedProcessors/pcie_ep_octeon_target.git ; \
    fi

WORKDIR /build/libconfig-1.7.2/
RUN if [ "$TARGETPLATFORM" = "linux/arm64" ] ; then \
        ./configure --host=aarch64-marvell-linux-gnu && \
        make all ; \
    fi

WORKDIR /build/pcie_ep_octeon_target/target/libs/octep_cp_lib/
RUN if [ "$TARGETPLATFORM" = "linux/arm64" ] ; then \
        make CFLAGS="-DUSE_PEM_AND_DPI_PF=1" ; \
    fi

WORKDIR /build/pcie_ep_octeon_target/target/apps/octep_cp_agent/
RUN if [ "$TARGETPLATFORM" = "linux/arm64" ] ; then \
        make CFLAGS="-I/build/libconfig-1.7.2/lib -I/build/pcie_ep_octeon_target/target/libs/octep_cp_lib/bin/include" \
             LDFLAGS="-L/build/libconfig-1.7.2/lib/.libs -L/build/pcie_ep_octeon_target/target/libs/octep_cp_lib/bin/lib" ; \
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

COPY --from=builder-octep-cp-agent /build/ /build/
COPY manifests/exec_octep_cp_agent /build/

RUN if [ "$TARGETPLATFORM" = "linux/arm64" ] ; then \
        mv /build/libconfig-1.7.2/lib/.libs/libconfig.so* /usr/lib/ && \
        mv /build/pcie_ep_octeon_target/target/apps/octep_cp_agent/bin/bin/octep_cp_agent \
           /build/pcie_ep_octeon_target/target/apps/octep_cp_agent/cn106xx.cfg \
           /usr/bin/ && \
        mv /build/exec_octep_cp_agent /usr/bin/ && \
        ldconfig ; \
    fi && \
    rm -rf /build/

ENV PATH="/marvell-octeon-10-tools:$PATH"
WORKDIR /marvell-octeon-10-tools
ENTRYPOINT ["/usr/bin/tini", "-s", "-p", "SIGTERM", "-g", "-e", "143", "--"]
CMD ["/usr/bin/sleep", "infinity"]
