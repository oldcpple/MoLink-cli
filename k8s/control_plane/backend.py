from flask import Flask, request, jsonify
import mysql.connector
from mysql.connector import errorcode
import subprocess
import json
from werkzeug.security import generate_password_hash, check_password_hash
import time
import re
import os
import requests
from pathlib import Path

class Config:
    ListenAddr = "0.0.0.0:12000"
    Kubeconfig = "/root/.kube/config"
    DbType = "mysql"
    DbHost = "127.0.0.1"
    DbPort = 3306
    DbName = "dkube"
    DbUser = "test"
    DbPwd = "zju123123"
    LogMode = True
    MaxIdleConns = 10
    MaxOpenConns = 100
    MaxLifeTime = 30  # seconds
    AdminUser = "admin"
    AdminPwd = "123456"

app = Flask(__name__)

# MySQL连接池配置
db_pool = None

def get_db_connection():
    global db_pool
    if not db_pool:
        db_pool = mysql.connector.pooling.MySQLConnectionPool(
            pool_name="k8s_pool",
            pool_size=Config.MaxIdleConns,
            host=Config.DbHost,
            port=Config.DbPort,
            user=Config.DbUser,
            password=Config.DbPwd,
            database=Config.DbName,
            pool_reset_session=True
        )
    return db_pool.get_connection()

def validate_kubeadm_token(token):
    try:
        result = subprocess.run(
            ['kubeadm', 'token', 'list', '-o', 'json'],
            stdout=subprocess.PIPE,
            check=True
        )
        output = result.stdout.decode().strip()
        # 替换多个JSON对象之间的换行符为逗号，并包裹成数组
        processed_output = output.replace('}\n{', '}, {')
        processed_output = f'[{processed_output}]'
        # 解析为JSON数组
        tokens = json.loads(processed_output)
        tokens = [token['token'] for token in tokens]
        return any(t == token for t in tokens)
    except Exception as e:
        app.logger.error(f"Token validation failed: {str(e)}")
        return False

@app.route('/k8s', methods=['POST'])
def k8s_join():
    data = request.json
    required_fields = ['token', 'hash', 'username', 'user_password']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400

    # 验证token有效性
    if not validate_kubeadm_token(data['token']):
        return jsonify({"error": "Invalid token"}), 401

    # 数据库验证
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        # 验证用户凭证
        cursor.execute("""
            SELECT password
            FROM users
            WHERE username = %s
        """, (data['username'],))
        user = cursor.fetchone()

        if not user:
            return jsonify({"error": "User not found"}), 401
        if not check_password_hash(generate_password_hash(user['password']), data['user_password']):
            return jsonify({"error": "Invalid password"}), 401

        return jsonify({"status": "200 OK"}), 200

    except mysql.connector.Error as err:
        app.logger.error(f"Database error: {err}")
        return jsonify({"error": "Database error"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/k8s_complete', methods=['POST'])
def join_complete():
    data = request.json
    if not data or 'username' not in data or 'hardware_info' not in data:
        return jsonify({"error": "Invalid request"}), 400

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id from users where username = %s""", (data['username'],)
        )
        user_id_result = cursor.fetchone()
        if not user_id_result:
            return jsonify({"error": "User not found"}), 500
        user_id = user_id_result[0]
        cursor.execute("""
            INSERT INTO node (name, ip, type, status, user_id, num_cpu, size_mem, num_gpu, gpu_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data['hardware_info'].get('name'),
            data['hardware_info'].get('ip'),
            0,
            0,
            user_id,
            data['hardware_info'].get('num_cpu'),
            data['hardware_info'].get('size_mem'),
            data['hardware_info'].get('num_gpu'),
            data['hardware_info'].get('gpu_type'),
        ))
        conn.commit()
        update_service_discovery(data['hardware_info'].get('ip'), 'node_exporters')
        update_service_discovery(data['hardware_info'].get('ip'), 'dcgm')
        return jsonify({"status": "Node registered"}), 200

    except mysql.connector.Error as err:
        app.logger.error(f"Database error: {err}")
        return jsonify({"error": "Failed to save node info"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/k8s_delete', methods=['POST'])
def k8s_delete():
    data = request.json
    required_fields = ['node_name', 'username', 'user_password']

    # 参数校验
    if not all(field in data for field in required_fields):
        return jsonify({"error": "缺少必要参数: node_name, username, user_password"}), 400

    node_name = data['node_name']
    # k8s requires all letters to be lower
    node_name_lower = node_name.lower()
    username = data['username']
    password = data['user_password']

    # 验证用户凭证
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # 查询用户信息
        cursor.execute("""
            SELECT password
            FROM users
            WHERE username = %s
        """, (username,))
        user = cursor.fetchone()

        if not user:
            return jsonify({"error": "用户不存在"}), 401
        if not check_password_hash(generate_password_hash(user['password']), data['user_password']):
            return jsonify({"error": "密码错误"}), 401

    except mysql.connector.Error as err:
        app.logger.error(f"数据库错误: {err}")
        return jsonify({"error": "数据库错误"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

    # 执行节点删除操作
    try:
        # 安全验证节点名称格式
        if not re.match(r'^[a-z0-9-]+$', node_name_lower):
            return jsonify({"error": "无效的节点名称"}), 400

        # 执行kubectl delete node
        cmd = [
            'kubectl', 'delete', 'node', node_name_lower,
            '--ignore-not-found=true'
        ]
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )

        # 删除数据库记录
        db_conn = None
        try:
            db_conn = get_db_connection()
            db_cursor = db_conn.cursor()

            # 执行删除操作
            db_cursor.execute("""
                DELETE FROM node
                WHERE name = %s
            """, (node_name,))
            deleted_rows = db_cursor.rowcount

            db_conn.commit()

            # 处理删除结果
            if deleted_rows == 0:
                app.logger.warning(f"数据库未找到节点记录: {node_name}")
                db_message = "节点数据库记录不存在"
            else:
                db_message = "节点数据库记录已删除"

        except mysql.connector.Error as err:
            app.logger.error(f"数据库删除失败: {err}")
            return jsonify({
                "error": "节点集群记录已删除，但数据库操作失败",
                "details": str(err)
            }), 500
        finally:
            if db_conn and db_conn.is_connected():
                db_cursor.close()
                db_conn.close()

        # 构造响应
        output = result.stdout.strip()
        if "deleted" in output:
            return jsonify({
                "status": "200 OK",
                "message": f"节点 {node_name} 删除成功",
                "details": {
                    "kubernetes": output,
                    "database": db_message
                }
            }), 200
        else:
            return jsonify({
                "status": "404 Not Found",
                "message": f"节点 {node_name} 不存在于集群中",
                "details": {
                    "database": db_message
                }
            }), 404

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or "未知错误"
        app.logger.error(f"节点删除失败: {error_msg}")
        return jsonify({
            "error": "节点删除失败",
            "details": error_msg
        }), 500

    except Exception as e:
        app.logger.error(f"系统错误: {str(e)}")
        return jsonify({
            "error": "系统内部错误",
            "details": str(e)
        }), 500

def update_service_discovery(new_ip: str, type:str, prometheus_url: str = "http://localhost:9999"):

    if type == 'node_exporters':
        json_path = '~/prometheus/prometheus-3.2.1.linux-amd64/node_exporters.json'
    if type == 'dcgm':
        json_path = '~/prometheus/prometheus-3.2.1.linux-amd64/dcgm.json'
    json_file = Path(json_path).absolute()
    json_file.parent.mkdir(parents=True, exist_ok=True)
    
    # 初始化默认数据结构
    data = [{"targets": []}]
    
    try:
        # 读取现有文件内容
        if json_file.exists():
            with open(json_file, 'r') as f:
                data = json.load(f)
                if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
                    raise ValueError("Invalid JSON structure")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"配置文件 {json_path} 格式错误，将创建新文件。错误详情: {str(e)}")
        data = [{"targets": []}]

    # 构造完整目标地址
    if type == 'node_exporters':
        new_target = f"{new_ip.strip()}:9100"
    if type == 'dcgm':
        new_target = f"{new_ip.strip()}:9400"
    
    # 检查是否已存在重复项
    existing_targets = set()
    for group in data:
        if "targets" in group and isinstance(group["targets"], list):
            existing_targets.update(group["targets"])
    
    if new_target in existing_targets:
        print(f"目标 {new_target} 已存在，无需重复添加")
        return

    # 添加新目标到第一个目标组（兼容多组结构）
    if data and "targets" in data[0]:
        data[0]["targets"].append(new_target)
    else:
        data = [{"targets": [new_target]}]

    # 写入更新后的文件
    with open(json_file, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"成功添加目标 {new_target} 到 {json_file}")

if __name__ == '__main__':
    app.run(
        host=Config.ListenAddr.split(':')[0],
        port=int(Config.ListenAddr.split(':')[1]),
        ssl_context='adhoc'
    )
