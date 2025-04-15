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

modify_containerd_config() {
  echo -e "${YELLOW}[2/5] 修改containerd配置...${NC}"
  
  # 生成默认配置（如果文件不存在）
  if [ ! -f /etc/containerd/config.toml ]; then
    containerd config default > /etc/containerd/config.toml
  fi

  containerd config default | tee /etc/containerd/config.toml

  # 修改sandbox_image和SystemdCgroup
  sed -i \
    -e 's|sandbox_image =.*|sandbox_image = "registry.aliyuncs.com/google_containers/pause:3.9"|g' \
    -e 's/SystemdCgroup =.*/SystemdCgroup = true/g' \
    /etc/containerd/config.toml

  systemctl restart containerd
  echo -e "${GREEN}containerd配置更新完成${NC}"
}

# 配置FTP参数
FTP_SERVER="10.202.210.104"  # 修改为实际FTP服务器IP
FTP_PORT="12001"
FTP_DIR="ftp_share"

# 新增：下载文件函数
download_from_ftp() {
    local filename=$1
    echo -e "${YELLOW}正在下载: ${filename}${NC}"
    
    if ! curl -s -S -O "ftp://${FTP_SERVER}:${FTP_PORT}/${FTP_DIR}/${filename}"; then
        echo -e "${RED}FTP下载失败: ${filename}${NC}"
        exit 1
    fi
    
    if [ ! -f "${filename}" ]; then
        echo -e "${RED}文件下载不完整: ${filename}${NC}"
        exit 1
    fi
}

# 修改后的镜像处理函数
pull_images() {
  echo -e "${YELLOW}[3/5] 开始处理镜像...${NC}"
  
  # 下载镜像文件
  download_from_ftp "cni.tar"
  download_from_ftp "node.tar"
  download_from_ftp "molink-release-0.8.tar"

  # 导入本地镜像
  for image in cni.tar node.tar molink-release-0.8.tar; do
    echo -e "${YELLOW}正在导入: ${image}${NC}"
    if ! ctr -n k8s.io images import "${image}"; then
      echo -e "${RED}镜像导入失败: ${image}${NC}"
      exit 1
    fi
    rm -f "${image}"  # 清理临时文件
  done

  # 拉取其他镜像
  other_images=(
    "registry.aliyuncs.com/google_containers/pause:3.9"
    "registry.aliyuncs.com/google_containers/kube-proxy:v1.28.15"
    "quay.io/prometheus/node-exporter:v1.8.2"
    "nvcr.io/nvidia/k8s/dcgm-exporter:3.3.5-3.4.0-ubuntu22.04"
    "nvcr.io/nvidia/k8s-device-plugin:v0.17.0"
  )

  for image in "${other_images[@]}"; do
    echo -e "${YELLOW}正在拉取: ${image}${NC}"
    if ! ctr -n k8s.io images pull "${image}"; then
      echo -e "${RED}镜像拉取失败: ${image}${NC}"
      exit 1
    fi
  done
}

# 安装NVIDIA设备插件
install_nvidia_plugin() {
  echo -e "${YELLOW}[4/5] 安装NVIDIA设备插件...${NC}"
  
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
  && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

  sed -i -e '/experimental/ s/^#//g' /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  apt-get install -y nvidia-container-toolkit

  # 配置containerd
  nvidia-ctk runtime configure --runtime=containerd --set-as-default
  systemctl restart containerd

  # 部署设备插件
  #echo -e "${YELLOW}[5/5] 部署NVIDIA设备插件...${NC}"
  #kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.17.1/deployments/static/nvidia-device-plugin.yml
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