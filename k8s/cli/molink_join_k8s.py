import argparse
import requests
import subprocess
import json
import socket
import psutil
import platform
import GPUtil
import os
import urllib3

urllib3.disable_warnings()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        return f"Error: {str(e)}"

def get_system_info():
    system_info = {}
    
    # 获取主机名称
    system_info['name'] = platform.node()
    system_info['ip'] = get_local_ip()

    
    # 获取 CPU 核数
    system_info['num_cpu'] = psutil.cpu_count(logical=False)
    
    # 获取主存大小
    virtual_memory = psutil.virtual_memory()
    system_info['size_mem'] = round(float(virtual_memory.total / (1024 ** 3)), 2)
    
    # 获取 GPU 卡数和 GPU 型号
    gpus = GPUtil.getGPUs()
    system_info['num_gpu'] = len(gpus)
    system_info['gpu_type'] = gpus[0].name
    return system_info

def join_cluster(args):
    """执行加入集群流程"""
    # 认证请求
    auth_url = f"https://{args.control_plane}:12000/k8s"
    auth_data = {
        "token": args.token,
        "hash": args.hash,
        "username": args.username,
        "user_password": args.password
    }
    
    try:
        # 发送认证请求
        auth_resp = requests.post(auth_url, json=auth_data, verify=False, timeout=10)
        auth_resp.raise_for_status()
        auth_result = auth_resp.json()
        
        if auth_result.get('status') != '200 OK':
            print(f"认证失败: {auth_result.get('error', '未知错误')}")
            return False

        print("认证成功，正在加入集群...")
        
        # 执行kubeadm join
        cmd = [
            'kubeadm', 'join', f'{args.control_plane}:6443',
            '--token', args.token,
            '--discovery-token-ca-cert-hash', args.hash
        ]
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300
        )
        print("成功加入集群")

        # 收集硬件信息
        hardware_info = get_system_info()
        print("\n硬件信息收集完成:")
        for k, v in hardware_info.items():
            print(f"{k:>12}: {v}")

        # 发送完成通知
        complete_url = f"https://{args.control_plane}:12000/k8s_complete"
        complete_data = {
            "username": args.username,
            "hardware_info": hardware_info
        }
        complete_resp = requests.post(
            complete_url, 
            json=complete_data, 
            verify=False,
            timeout=10
        )
        complete_resp.raise_for_status()
        print("\n节点信息已成功上报")

        folder_name = "molink_log"
        control_plane_log = "control_plane.txt"
        molink_log = "log.txt"
        current_path = os.getcwd()
        folder_path = os.path.join(current_path, folder_name)

        # 检查目标文件夹是否存在，如果不存在则创建
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            print(f"文件夹 '{folder_name}' 已创建。")
        else:
            print(f"文件夹 '{folder_name}' 已存在。")

        file_path = os.path.join(folder_path, control_plane_log)

        with open(file_path, "w") as file:
            file.write(f"control plane at: {args.control_plane}")

        print(f"文件 '{control_plane_log}' 已在文件夹 '{folder_name}' 中创建。")

        file_path = os.path.join(folder_path, molink_log)

        with open(file_path, "w") as file:
            file.write("")

        print(f"文件 '{molink_log}' 已在文件夹 '{folder_name}' 中创建。")

        return True

    except requests.exceptions.RequestException as e:
        print(f"网络请求失败: {str(e)}")
    except subprocess.CalledProcessError as e:
        print(f"命令执行失败: {e.stderr}")
    except Exception as e:
        print(f"发生未知错误: {str(e)}")
    
    return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Kubernetes节点加入客户端',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('control_plane', help='控制平面地址（IP:端口）')
    parser.add_argument('token', help='加入令牌')
    parser.add_argument('hash', help='CA证书哈希值')
    parser.add_argument('username', help='认证用户名')
    parser.add_argument('password', help='认证密码')
    
    args = parser.parse_args()
    
    if not join_cluster(args):
        print("\n节点加入流程失败，请检查：")
        print("1. 网络连接是否正常")
        print("2. 令牌和哈希值是否有效")
        print("3. 用户名密码是否正确")
        print("4. 是否具有root权限")
        exit(1)