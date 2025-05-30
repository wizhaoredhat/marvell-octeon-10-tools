#!/bin/bash

sudo systemctl disable firewalld
sudo systemctl stop firewalld
sudo dnf remove -y zram-generator-defaults

# From: https://kubernetes.io/docs/setup/production-environment/container-runtimes/
cat <<EOF | sudo tee /etc/modules-load.d/crio.conf
overlay
br_netfilter
EOF

sudo modprobe overlay
sudo modprobe br_netfilter

cat <<EOF | sudo tee /etc/sysctl.d/99-kubernetes-cri.conf
net.bridge.bridge-nf-call-iptables  = 1
net.ipv4.ip_forward                 = 1
net.bridge.bridge-nf-call-ip6tables = 1
EOF

sudo sysctl --system

# From: https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/

# Set SELinux in permissive mode (effectively disabling it)
sudo setenforce 0
sudo sed -i 's/^SELINUX=enforcing$/SELINUX=permissive/' /etc/selinux/config

# This overwrites any existing configuration in /etc/yum.repos.d/kubernetes.repo
cat <<EOF | sudo tee /etc/yum.repos.d/kubernetes.repo
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/v1.29/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/v1.29/rpm/repodata/repomd.xml.key
exclude=kubelet kubeadm kubectl cri-tools kubernetes-cni
EOF

sudo yum install -y kubelet kubeadm kubectl --disableexcludes=kubernetes
sudo systemctl enable --now kubelet

# From: https://github.com/cri-o/cri-o/blob/main/install.md#readme
export PROJECT_PATH=prerelease:/main
cat <<EOF | tee /etc/yum.repos.d/cri-o.repo
[cri-o]
name=CRI-O
baseurl=https://pkgs.k8s.io/addons:/cri-o:/$PROJECT_PATH/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/addons:/cri-o:/$PROJECT_PATH/rpm/repodata/repomd.xml.key
EOF

sudo dnf remove -y crun

export VERSION=1.22
sudo dnf -y module enable cri-o:$VERSION
sudo dnf install -y cri-o
sudo systemctl daemon-reload
sudo systemctl enable crio --now

# https://github.com/kubernetes/kubeadm/issues/610
sudo swapoff -a

kubeadm init --pod-network-cidr=10.244.0.0/16

mkdir -p "$HOME/.kube"
sudo cp -i /etc/kubernetes/admin.conf "$HOME/.kube/config"
sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config"
export KUBECONFIG=/etc/kubernetes/admin.conf

dnf install -y kubernetes-cni
kubectl apply -f https://raw.githubusercontent.com/coreos/flannel/master/Documentation/kube-flannel.yml
kubectl apply -f https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/master/deployments/multus-daemonset-thick.yml

# From: https://stackoverflow.com/questions/61373366/networkplugin-cni-failed-to-set-up-pod-xxxxx-network-failed-to-set-bridge-add
ip link set cni0 down && ip link set flannel.1 down
ip link delete cni0 && ip link delete flannel.1
sudo systemctl restart crio
sudo systemctl restart kubelet
