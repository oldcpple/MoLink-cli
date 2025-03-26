#!/bin/bash
set -eo pipefail

# 定义颜色常量
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# 检查是否为root用户
if [ "$(id -u)" -ne 0 ]; then
  echo -e "${RED}错误：本脚本需要使用root权限运行${NC}"
  exit 1
fi

# 函数：检查命令是否存在
check_command() {
  if ! command -v "$1" &> /dev/null; then
    echo -e "${RED}错误：未找到 $1 命令${NC}"
    exit 1
  fi
}

# 检查必要命令
check_command containerd
check_command ctr
check_command curl
check_command sed
check_command tee
check_command kubectl

# 备份原始配置文件
backup_containerd_config() {
  echo -e "${YELLOW}[1/5] 备份containerd配置文件...${NC}"
  cp /etc/containerd/config.toml /etc/containerd/config.toml.bak.$(date +%Y%m%d%H%M%S)
}

# 修改containerd配置
modify_containerd_config() {
  echo -e "${YELLOW}[2/5] 修改containerd配置...${NC}"
  
  # 生成默认配置（如果文件不存在）
  if [ ! -f /etc/containerd/config.toml ]; then
    containerd config default > /etc/containerd/config.toml
  fi

  # 修改sandbox_image和SystemdCgroup
  sed -i \
    -e 's|sandbox_image =.*|sandbox_image = "registry.aliyuncs.com/google_containers/pause:3.9"|g' \
    -e 's/SystemdCgroup =.*/SystemdCgroup = true/g' \
    /etc/containerd/config.toml

  # 添加镜像加速配置
  cat >> /etc/containerd/config.toml <<EOF

[plugins."io.containerd.grpc.v1.cri".registry.mirrors]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."docker.elastic.co"]
    endpoint = ["https://elastic.m.daocloud.io"]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."docker.io"]
    endpoint = ["https://6qxc6b6n.mirror.aliyuncs.com", "https://docker.m.daocloud.io", "https://dockerproxy.com/"]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."gcr.io"]
    endpoint = ["https://gcr.m.daocloud.io", "https://gcr.nju.edu.cn", "https://gcr.dockerproxy.com"]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."ghcr.io"]
    endpoint = ["https://ghcr.m.daocloud.io", "https://ghcr.nju.edu.cn"]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."k8s.gcr.io"]
    endpoint = ["https://k8s-gcr.m.daocloud.io", "https://gcr.nju.edu.cn/google-containers/", "https://k8s.dockerproxy.com/"]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."quay.io"]
    endpoint = ["https://quay.m.daocloud.io", "https://quay.nju.edu.cn"]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."registry.k8s.io"]
    endpoint = ["https://k8s.m.daocloud.io", "https://k8s.nju.edu.cn"]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."registry.nvcr.io"]
    endpoint = ["https://nvcr.nju.edu.cn", "https://ngc.nju.edu.cn"]
EOF

  systemctl restart containerd
  echo -e "${GREEN}containerd配置更新完成${NC}"
}

# 拉取必要镜像
pull_images() {
  echo -e "${YELLOW}[3/5] 开始拉取镜像...${NC}"
  images=(
    "docker.io/calico/cni:v3.27.3"
    "docker.io/calico/node:v3.27.3"
    "registry.aliyuncs.com/google_containers/pause:3.9"
    "registry.aliyuncs.com/google_containers/kube-proxy:v1.28.15"
    "quay.io/prometheus/node-exporter:v1.8.2"
    "nvcr.io/nvidia/k8s/dcgm-exporter:4.0.0-4.0.1-ubuntu22.04"
  )

  for image in "${images[@]}"; do
    echo -e "${YELLOW}正在拉取: ${image}${NC}"
    if ! ctr images pull "${image}"; then
      echo -e "${RED}镜像拉取失败: ${image}${NC}"
      exit 1
    fi
  done
}

# 安装NVIDIA设备插件
install_nvidia_plugin() {
  echo -e "${YELLOW}[4/5] 安装NVIDIA设备插件...${NC}"
  
  # 添加NVIDIA仓库源
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

  apt-get update -qq
  apt-get install -y -qq nvidia-container-toolkit

  # 配置containerd
  nvidia-ctk runtime configure --runtime=containerd --set-as-default
  systemctl restart containerd

  # 部署设备插件
  echo -e "${YELLOW}[5/5] 部署NVIDIA设备插件...${NC}"
  kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.17.1/deployments/static/nvidia-device-plugin.yml
}

# 主执行流程
main() {
  backup_containerd_config
  modify_containerd_config
  pull_images
  install_nvidia_plugin
  
  echo -e "\n${GREEN}所有操作已完成！${NC}"
  echo -e "请检查以下服务状态："
  echo -e "1. containerd 状态: systemctl status containerd"
  echo -e "2. 节点设备状态: kubectl get nodes -o wide"
}

# 执行主函数
main