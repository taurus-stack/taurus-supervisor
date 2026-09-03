# Taurus Supervisor

轻量级通用进程管理守护进程，负责管理任意程序的生命周期。

## 概述

Taurus Supervisor 是一个**通用的程序管理守护进程**，运行在目标主机上，可以管理任意数量的程序（如 taurus-executor、taurus-monitor、自定义业务进程等），负责：

- **进程管理**：启动、停止、重启任意受管程序
- **升级管理**：下载、验证、安装、切换新版本（零停机升级）
- **健康检查**：定期检查程序健康状态
- **故障恢复**：崩溃自动恢复、版本回滚
- **版本管理**：维护版本历史和状态
- **心跳上报**：向 Server 上报程序状态和系统指标
- **指令执行**：接收并执行 Server 下发的管理指令

## 架构

### 整体架构

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│  Taurus Server   │◄────────│  Taurus Supervisor│────────►│  Target Host    │
│  (Backend)      │ 心跳/指令│  (守护进程)       │ 管理程序 │  (远程主机)     │
└─────────────────┘         └────────┬─────────┘         └─────────────────┘
                                     │
                              ┌──────┴───────┐
                              │ 任意受管程序   │
                              │ - executor    │
                              │ - monitor     │
                              │ - agent       │
                              │ - custom-app  │
                              │ - ...         │
                              └──────────────┘
```

### 组件关系

```
taurus-supervisor（守护进程，常驻运行）
    ↓ 管理任意数量的程序
├── taurus-executor（业务进程，可被替换）
├── taurus-monitor（监控进程）
├── taurus-agent（代理进程）
├── custom-app（自定义应用）
└── ...（任意其他进程）
```

## 核心特性

### 1. 通用程序管理

Supervisor 是一个**通用的进程管理器**，可以管理任意程序，不限于 taurus 系列：

- **任意程序**：可以管理 taurus-executor、taurus-monitor、自定义业务进程、第三方应用等
- **独立配置**：每个程序独立版本、独立进程控制、独立健康检查、独立升级/回滚
- **动态注册**：通过 Server 下发指令动态注册新程序，无需修改 Supervisor 代码
- **统一管控**：一个 Supervisor 实例可管理多个不同类型的程序

**典型应用场景**：

| 场景 | 示例程序 |
|------|---------|
| 远程命令执行 | taurus-executor |
| 系统监控采集 | taurus-monitor、prometheus-node-exporter |
| 日志采集 | filebeat、fluentd |
| 安全代理 | osquery、falco |
| 自定义业务进程 | 任意可执行文件 |

### 2. 零停机升级

升级过程中服务不中断：

```
时间线：
下载（30 秒 - 5 分钟）  → 老版本服务正常 ✅
安装（1-2 秒）         → 老版本服务正常 ✅
启动新进程（3 秒）     → 老版本服务正常 ✅
健康检查（5 秒）       → 老版本服务正常 ✅
切换（< 1 秒）         → 瞬间切换 ⚡
```

**总中断时间：< 1 秒**（仅在切换瞬间）

### 3. 自动故障恢复

| 场景 | 恢复策略 |
|------|---------|
| 程序崩溃 | Supervisor 自动重启当前版本 |
| 升级失败 | 保持老版本运行，上报失败 |
| 新版本启动失败 | 回滚到老版本 |
| 断电重启 | 从 state.json 恢复到最后稳定版本 |

### 4. 心跳与指令系统

- **定期心跳**：上报程序状态、系统指标（CPU、内存、磁盘、网络）
- **指令下发**：接收 Server 的安装、升级、启停指令
- **指令持久化**：进程重启后恢复未执行指令
- **服务器故障转移**：支持多 Server 节点，自动切换

### 5. TLS 证书管理

- 自动申请和续期客户端证书
- 证书吊销即时响应
- mTLS 双向认证保障通信安全

## 目录结构

### Root 用户部署

```
/opt/taurus/                          # 基础目录
├── supervisor/                      # Supervisor 守护进程
│   ├── bin/
│   │   └── taurus-supervisor         # Supervisor 二进制
│   └── templates/
│       └── systemd/
│           └── taurus-supervisor.service
│
├── versions/                        # 程序版本目录
│   ├── v1.0.0/
│   │   ├── taurus-executor/          # 程序 1.0.0 版本
│   │   │   ├── taurus-executor       # 程序二进制
│   │   │   ├── .env                 # 程序配置
│   │   │   └── tls/                 # TLS 证书
│   │   └── taurus-monitor/           # 程序 1.0.0 版本
│   │       └── ...
│   ├── v1.1.0/
│   │   └── ...
│   └── current -> v1.1.0           # 符号链接（当前版本）
│
└── data/                            # 数据目录
    └── state.json                   # Supervisor 状态文件
```

### 普通用户部署

```
~/taurus/                             # 基础目录
├── supervisor/                      # Supervisor 守护进程
│   └── bin/
│       └── taurus-supervisor
│
├── versions/                        # 程序版本目录
│   └── v1.0.0/
│       └── taurus-executor/
│           ├── taurus-executor
│           ├── .env
│           └── tls/
│
└── data/
    └── state.json
```

## 安装

### 方式一：一键自动安装（推荐）

使用注册脚本自动完成注册、下载、安装、配置和启动：

#### Root 用户

```bash
sudo ./scripts/register.sh \
  --server http://<taurus-server>:8000 \
  --token <your-token> \
  --auto-install
```

**自动配置**：
- ✅ 基础目录：`/opt/taurus`
- ✅ Supervisor 目录：`/opt/taurus/supervisor`
- ✅ 版本目录：`/opt/taurus/versions/v{version}/`
- ✅ 配置目录：`/etc/taurus-supervisor`
- ✅ 服务类型：systemd 系统服务
- ✅ 开机自启：是

#### 普通用户

```bash
./scripts/register.sh \
  --server http://<taurus-server>:8000 \
  --token <your-token> \
  --auto-install
```

**自动配置**：
- ✅ 基础目录：`~/taurus`
- ✅ Supervisor 目录：`~/taurus/supervisor`
- ✅ 版本目录：`~/taurus/versions/v{version}/`
- ✅ 配置目录：`~/.taurus-supervisor`
- ✅ 服务类型：systemd --user 或后台进程
- ✅ 开机自启：取决于 linger 配置

### 方式二：手动安装

#### 1. 构建

```bash
cd taurus-supervisor
bash scripts/build.sh --version 1.0.0
```

#### 2. 注册

```bash
./scripts/register.sh \
  --server http://<taurus-server>:8000 \
  --token <your-token>
```

#### 3. 部署

```bash
# 创建目录
sudo mkdir -p /opt/taurus/supervisor
sudo mkdir -p /etc/taurus-supervisor
sudo mkdir -p /var/log/taurus-supervisor

# 安装
sudo tar xzf dist/taurus-supervisor-1.0.0-linux-x86_64.tar.gz -C /opt/taurus/supervisor

# 配置
sudo cp templates/supervisor.env.example /etc/taurus-supervisor/supervisor.env
sudo vim /etc/taurus-supervisor/supervisor.env

# 安装 systemd 服务
sudo cp templates/systemd/taurus-supervisor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable taurus-supervisor
sudo systemctl start taurus-supervisor
```

## 配置

### 环境变量文件

**Root 用户**：`/etc/taurus-supervisor/supervisor.env`

**普通用户**：`~/.taurus-supervisor/supervisor.env`

```bash
# 基础目录
BASE_DIR=/opt/taurus

# Server 地址
SERVER_URL=http://localhost:8000

# 主机 ID（注册后自动填充）
HOST_ID=xxx-xxx-xxx

# 健康检查间隔（秒）
HEALTH_CHECK_INTERVAL=30

# Supervisor 心跳间隔（秒）
SUPERVISOR_HEARTBEAT_INTERVAL=30

# TLS 证书目录
TLS_DIR=/etc/taurus-supervisor/tls

# 日志配置
LOG_DIR=/var/log/taurus-supervisor
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
```

### 状态文件

`/opt/taurus/data/state.json` 或 `~/taurus/data/state.json`：

```json
{
  "host_id": "xxx-xxx-xxx",
  "current_version": "1.1.0",
  "previous_version": "1.0.0",
  "upgrade_in_progress": false,
  "upgrade_target_version": null,
  "last_successful_version": "1.1.0",
  "upgrade_history": [
    {
      "from_version": "1.0.0",
      "to_version": "1.1.0",
      "timestamp": 1234567890,
      "status": "success"
    }
  ],
  "programs": {
    "taurus-executor": {
      "pid": 12345,
      "port": 50051,
      "status": "running",
      "version": "1.1.0"
    },
    "taurus-monitor": {
      "pid": 12346,
      "port": 9090,
      "status": "running",
      "version": "1.0.0"
    }
  }
}
```

## 使用

### 服务管理

#### Root 用户（systemd 系统服务）

```bash
# 查看状态
sudo systemctl status taurus-supervisor

# 查看日志
sudo journalctl -u taurus-supervisor -f

# 重启/停止/启用
sudo systemctl restart taurus-supervisor
sudo systemctl stop taurus-supervisor
sudo systemctl enable taurus-supervisor
sudo systemctl disable taurus-supervisor
```

#### 普通用户（systemd --user）

```bash
# 查看状态
systemctl --user status taurus-supervisor

# 查看日志
journalctl --user -u taurus-supervisor -f

# 重启/停止/启用
systemctl --user restart taurus-supervisor
systemctl --user stop taurus-supervisor
systemctl --user enable taurus-supervisor
systemctl --user disable taurus-supervisor
```

#### 普通用户（后台进程模式）

```bash
# 查看进程
ps aux | grep taurus-supervisor

# 查看日志
tail -f ~/taurus/supervisor.log

# 重启服务
kill $(cat ~/taurus/supervisor.pid) && ~/taurus/start-supervisor.sh

# 停止服务
kill $(cat ~/taurus/supervisor.pid)
```

### 手动升级

```bash
# 通过 API 触发
curl -X POST http://localhost:8000/api/taurus/host/{host_id}/trigger_update/ \
  -H "Authorization: Bearer <token>" \
  -d '{"target_version": "1.1.0"}'
```

### 查看程序状态

```bash
# 查看状态文件
cat /opt/taurus/data/state.json | jq .programs

# 查看进程
ps aux | grep taurus-

# 查看端口
ss -tlnp | grep 5005
```

## 程序管理流程

### 程序安装流程（首次安装）

```
1. Server 创建 ProgramInstallConfig（指定程序名称、版本、配置）
   ↓
2. Supervisor 心跳时接收 install 指令
   ↓
3. 从 Server 下载程序包到 versions/v{version}/{program_name}/
   ↓
4. 验证 SHA256 checksum
   ↓
5. 解压/安装程序到版本目录
   ↓
6. 生成程序配置文件（.env、tls/ 等）
   ↓
7. 启动程序进程
   ↓
8. 健康检查（等待 5 秒）
   ↓
9. 健康检查通过
   ↓
10. 更新状态文件（state.json）
   ↓
11. 上报程序运行状态到 Server
   ↓
12. 安装完成 ✅
```

### 程序升级流程（零停机）

```
1. Server 下发 upgrade 指令（指定目标版本）
   ↓
2. Supervisor 下载新版本到 versions/v{new_version}/{program_name}/
   ↓
3. 验证 SHA256 checksum
   ↓
4. 启动新版本（使用临时端口，如原端口 +1）
   ↓
5. 健康检查（等待 5 秒）
   ↓
6. 健康检查通过
   ↓
7. 停止老版本进程
   ↓
8. 更新状态文件（切换当前版本）
   ↓
9. 上报升级成功到 Server
   ↓
10. 升级完成 ✅（总中断时间 < 1 秒）
```

### 程序启停流程

**启动程序**：
```
1. Server 下发 start 指令
   ↓
2. Supervisor 检查程序版本目录是否存在
   ↓
3. 如不存在，自动下载
   ↓
4. 启动程序进程
   ↓
5. 上报运行状态
```

**停止程序**：
```
1. Server 下发 stop 指令
   ↓
2. Supervisor 发送 SIGTERM 信号
   ↓
3. 等待进程退出（默认 10 秒）
   ↓
4. 如未退出，发送 SIGKILL 强制终止
   ↓
5. 更新状态文件
   ↓
6. 上报已停止状态
```

**重启程序**：
```
1. Server 下发 restart 指令
   ↓
2. 执行 stop 流程
   ↓
3. 等待 1 秒
   ↓
4. 执行 start 流程
```

### 程序卸载流程

```
1. Server 下发 remove 指令
   ↓
2. Supervisor 停止程序进程
   ↓
3. 删除版本目录（versions/v{version}/{program_name}/）
   ↓
4. 清理状态文件中的程序记录
   ↓
5. 上报已卸载状态
   ↓
6. 卸载完成 ✅
```

### 升级失败回滚

```
新版本健康检查失败
    ↓
停止新进程
    ↓
保持老版本运行
    ↓
上报升级失败
    ↓
等待下次升级指令
```

## 故障恢复

### 场景 1：程序崩溃

```
taurus-supervisor 检测到进程退出
    ↓
尝试重启当前版本
    ↓
如果连续失败（默认 5 次），停止自动重启
    ↓
上报程序崩溃状态
```

### 场景 2：升级失败

```
新版本健康检查失败
    ↓
停止新进程
    ↓
回滚到老版本
    ↓
上报升级失败
```

### 场景 3：断电恢复

```
系统重启
    ↓
systemd 启动 taurus-supervisor
    ↓
taurus-supervisor 读取 state.json
    ↓
恢复到最后稳定版本
    ↓
启动所有 auto_start=true 的程序
```

### 场景 4：Server 不可用

```
心跳连续失败（默认 5 次）
    ↓
切换到备用 Server（如果配置）
    ↓
如果所有 Server 不可用，继续运行当前程序
    ↓
等待 Server 恢复
```

## 心跳机制

### 心跳内容

Supervisor 定期向 Server 发送心跳，包含：

- 主机信息（host_id, host_name, host_username）
- Supervisor 版本
- 所有受管程序状态（名称、版本、状态、PID、端口）
- 系统指标（CPU、内存、磁盘、网络、负载、运行时间）
- 时间戳

### 心跳响应

Server 响应可能包含：

- **证书吊销指令**：通知客户端证书已被吊销
- **程序安装指令**：安装新程序
- **程序升级指令**：升级现有程序
- **程序启停指令**：启动、停止、重启程序
- **程序卸载指令**：移除程序

### 指令持久化

- 指令保存到 `pending_commands.json` 文件
- 进程重启后自动恢复未执行指令
- 支持重试机制（记录重试次数和错误信息）

## 日志

### 日志文件

**Root 用户**：`/var/log/taurus-supervisor/supervisor.log`

**普通用户**：`~/taurus/supervisor.log`

### 日志格式

```
2024-01-01 12:00:00 - taurus-supervisor - INFO - ==========================================
2024-01-01 12:00:00 - taurus-supervisor - INFO - Taurus Supervisor 启动
2024-01-01 12:00:00 - taurus-supervisor - INFO - ==========================================
2024-01-01 12:00:01 - taurus-supervisor - INFO - 恢复状态: 当前版本 1.1.0
2024-01-01 12:00:01 - taurus-supervisor - INFO - 注册程序: taurus-executor v1.1.0
2024-01-01 12:00:01 - taurus-supervisor - INFO - 启动程序: taurus-executor v1.1.0
2024-01-01 12:00:04 - taurus-supervisor - INFO - 程序已启动: taurus-executor PID=12345 PORT=50051
2024-01-01 12:00:04 - taurus-supervisor - INFO - 程序 1.1.0 启动成功
2024-01-01 12:00:30 - taurus-supervisor - INFO - Supervisor 心跳已启动: interval=30s, ssl_verify=True
```

### 日志轮转

- 使用 `RotatingFileHandler`
- 默认单文件最大 10MB
- 默认保留 5 个备份文件
- 可通过环境变量配置

## 安全

### TLS 证书

- 使用 mTLS 双向认证
- 证书由 Server 统一签发
- 支持证书吊销和续期
- 证书目录权限：700

### 请求签名

- 支持 HMAC-SHA256 请求签名
- 防止请求篡改和重放攻击
- 签名密钥由 Server 分配

### 权限控制

- Root 用户：可切换运行用户
- 普通用户：以当前用户运行
- 程序二进制权限：755
- 配置文件权限：600

## 多实例部署

在同一台主机上部署多个 Supervisor 实例：

```bash
# 实例 1
sudo ./scripts/register.sh \
  --server http://<server>:8000 \
  --token <token1> \
  --auto-install \
  --install-dir /opt/taurus-1

# 实例 2
sudo ./scripts/register.sh \
  --server http://<server>:8000 \
  --token <token2> \
  --auto-install \
  --install-dir /opt/taurus-2
```

**注意**：每个实例需要不同的 token 和安装目录，端口会自动分配避免冲突。

## 常见问题

### Q1: Supervisor 只能管理 taurus-executor 吗？

**A**: 不是！Supervisor 是一个**通用的进程管理器**，可以管理任意程序。只要程序有可执行文件，就可以通过 Server 下发指令注册到 Supervisor 中进行管理。

### Q2: 如何添加自定义程序到 Supervisor 管理？

**A**: 通过 Server API 创建 `ProgramInstallConfig`，指定程序名称、版本、配置等，Supervisor 心跳时会自动接收指令并安装管理。例如：

```bash
# 安装自定义程序
curl -X POST http://localhost:8000/api/taurus/program-install-config/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "host": 1,
    "program_name": "my-custom-app",
    "version": "1.0.0",
    "config": {
      "port": 8080,
      "auto_start": true,
      "restart_on_crash": true,
      "env_vars": {"NODE_ENV": "production"}
    },
    "auto_start": true
  }'

# 升级自定义程序
curl -X POST http://localhost:8000/api/taurus/program-command/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "host": 1,
    "program_name": "my-custom-app",
    "action": "upgrade",
    "target_version": "1.1.0"
  }'

# 启动/停止/重启自定义程序
curl -X POST http://localhost:8000/api/taurus/program-command/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{
    "host": 1,
    "program_name": "my-custom-app",
    "action": "start"  # 或 stop、restart、remove
  }'
```

### Q3: Supervisor 和 Executor 的关系？

**A**: Supervisor 是守护进程，负责管理任意程序的生命周期。Executor 只是其中一个被管理的业务进程，执行具体的命令和任务。

### Q4: 如何查看程序运行状态？

**A**: 查看状态文件 `cat /opt/taurus/data/state.json` 或通过 Server API 查询。

### Q5: 升级失败怎么办？

**A**: Supervisor 会自动回滚到老版本，无需手动干预。

### Q6: 如何手动安装程序？

**A**: 通过 Server API 创建 `ProgramInstallConfig`，Supervisor 心跳时会自动接收并安装。

### Q7: 支持哪些操作系统？

**A**: 目前支持 Linux（x86_64, arm64），未来可能支持 macOS 和 Windows。

### Q8: 如何配置备用 Server？

**A**: 在注册时 Server 会自动返回备用节点列表，保存在配置文件中。

## 相关文档

- [Taurus Executor 部署文档](../taurus-executor/docs/deployment.md)
- [Supervisor 程序管理文档](../taurus-backend/docs/supervisor_program_management.md)
- [Supervisor 通信协议](docs/communication.md)
- [通用程序管理方案](../taurus-executor/docs/upgrade-scheme.md)

## 开发

### 构建

```bash
bash scripts/build.sh --version 1.0.0
```

### 本地测试

```bash
# 设置环境变量
export BASE_DIR=/tmp/taurus-test
export SERVER_URL=http://localhost:8000
export HOST_ID=test-host-id

# 运行 Supervisor
python -m taurus_supervisor.main
```

### 依赖

- Python 3.8+
- aiohttp
- psutil
- cryptography

## 许可证

本项目采用 GNU Affero General Public License v3.0 - 详见 [LICENSE](LICENSE) 文件。

## 联系方式

- 邮箱: taurus-stack@outlook.com
- 问题反馈: [GitHub Issues](https://github.com/taurus-ops/taurus-supervisor/issues)