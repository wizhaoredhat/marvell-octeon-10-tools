FROM quay.io/centos/centos:stream9 

RUN dnf install -y \
	tftp tftp-server dhcp-server python39 python3-pip minicom procps && \
	ln -s /usr/bin/python3.9 /usr/bin/python && \	
	pip3.9 install requests && \
	pip3.9 install pexpect

COPY * /
COPY manifests /manifests

RUN mv /manifests/.minirc.dfl /root/
