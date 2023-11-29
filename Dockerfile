FROM quay.io/centos/centos:stream9 

RUN dnf install -y \
	tftp tftp-server dhcp-server python39 && \
	ln -s /usr/bin/pip3.9 /usr/bin/pip && \
        ln -s /usr/bin/python3.9 /usr/bin/python

COPY * /
