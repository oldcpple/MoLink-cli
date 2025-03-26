# node_remove.py
import argparse
import requests
import subprocess
import platform
import re
import json
import urllib3

urllib3.disable_warnings()

def get_node_name():
    """获取当前节点名称（需与k8s集群注册名称一致）"""
    return platform.node().strip()

def remove_from_cluster(args):
    """执行退出集群流程"""
    # 构造请求URL
    delete_url = f"https://{args.master}:12000/k8s_delete"

    # 获取节点名称
    node_name = get_node_name()
    print(f"当前节点名称: {node_name}")

    # 准备请求数据
    payload = {
        "node_name": node_name,
        "username": args.username,
        "user_password": args.password
    }

    print(delete_url, node_name, args.username, args.password)
    

    try:
        # 发送删除请求
        response = requests.post(
            delete_url,
            json=payload,
            verify=False,  # 生产环境应使用有效证书
            timeout=10
        )
        response.raise_for_status()
        
        # 处理响应
        result = response.json()
        if response.status_code == 200:
            print("\n[1/2] 集群删除成功:", result.get("message"))
            print("正在执行本地清理...")
            
            # 执行kubeadm reset
            reset_result = subprocess.run(
                ["kubeadm", "reset", "-f"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300
            )
            print("\n[2/2] 本地清理完成:")
            print(reset_result.stdout)
            return True
        else:
            print("删除失败:", result.get("error"))
            return False

    except requests.exceptions.RequestException as e:
        print(f"\n请求失败: {str(e)}")
        if hasattr(e, 'response') and e.response.text:
            print("服务端响应:", e.response.text)
    except subprocess.CalledProcessError as e:
        print(f"\n清理失败: {e.stderr}")
    except Exception as e:
        print(f"\n发生未知错误: {str(e)}")
    
    return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Kubernetes节点退出工具',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        'master', 
        help='控制平面地址 (IP) 例: 192.168.1.100'
    )
    parser.add_argument('username', help='认证用户名')
    parser.add_argument('password', help='认证密码')
    
    args = parser.parse_args()
    
    print(f"正在从集群 {args.master}:6443 退出节点...")
    if remove_from_cluster(args):
        print("\n操作成功完成！请手动：")
        print("1. 检查网络配置清理")
        print("2. 删除残留的kubeconfig文件")
    else:
        print("\n退出流程失败，请检查：")
        print(f"1. 节点名称是否正确（当前名称：{get_node_name()}）")
        print("2. 网络连接是否正常")
        print("3. 认证信息是否正确")
        exit(1)