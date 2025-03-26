# molink_log_service.py
from flask import Flask, jsonify
import os
from collections import deque

app = Flask(__name__)
LOG_FILE = './molink_log.txt'
PORT = 12000

def get_last_n_lines(n=10):
    """获取日志文件最后n行"""
    try:
        with open(LOG_FILE, 'r') as f:
            return list(deque(f, maxlen=n))
    except FileNotFoundError:
        return None

@app.route('/molink_log', methods=['POST'])
def handle_log_request():
    # 读取日志文件
    lines = get_last_n_lines(10)
    
    if lines is None:
        return jsonify({
            "status": "error",
            "message": "Log file not found"
        }), 404
    
    return jsonify({
        "status": "success",
        "lines": [line.strip() for line in lines]
    })

if __name__ == '__main__':
    # 只监听本地回环地址
    app.run(host='0.0.0.0', port=PORT)