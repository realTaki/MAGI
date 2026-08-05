# MAGI 单机本地部署（非容器 / CLI）

这是 MAGI 提供的**单机、非容器**部署方式。意图是：

- 一个 `magi cli start` 命令就能启动一个 MAGI；
- 每个 MAGI 是独立 OS 进程，一个崩溃不影响其他；
- 持久化数据放在一个明显的目录里，方便备份、复制、迁移；
- 仅依赖系统已有的 Python / systemd，不引入 Docker / k8s；
- 可以通过 `magi cli install-service` 为每个 MAGI 注册独立的
  systemd 用户单元，不需要 root 权限。

这条路径适合：单机开发者、本地评审、单机 PoC、小团队自托管。

> 译注：原本这条路径叫 "local"——但 `k8s-dev`（kind 单机模拟）也是
> 一种 "local" 运行方式，名字会与 `deploy/k8s-dev/` 混淆。因此改名
> 为 **cli**（非容器、命令行驱动）；它和 `k8s`（生产 K8s 集群）与
> `k8s-dev`（kind 单机 + HMR）一起构成三条部署路径。

| 平台 | 数据根 | 类型 |
| --- | --- | --- |
| Linux | `~/.magi/` | 隐藏目录 |
| macOS | `~/Documents/.magi/` | 放在 Documents 下 |
| Windows | `%USERPROFILE%\Documents\.magi\` | 放在 Documents 下 |

`$HOST_WORKSPACE_DIR` 始终优先于上述默认路径。

## 快速开始

```bash
# 1. 安装
./deploy/cli/install.sh

# 2. 启动 Adam（第一个 MAGI，前台 + 自动打开浏览器）
./deploy/cli/magi cli start

# 3. 启动其他 MAGI（如 eva-00）
./deploy/cli/magi cli start --name eva-00 --port 42070

# 4. （可选）为所有 MAGI 注册 systemd user unit
./deploy/cli/magi cli install-service

# 5. 卸载服务
./deploy/cli/magi cli uninstall-service
```

> Bash 包装 `deploy/cli/magi` 存在的目的：固化 `HOST_WORKSPACE_DIR`
> 和 `MAGIS_DATABASE_URL` 默认值，让 `magi cli` 调用无需记
> 手动设环境变量。如果你已经 `uv tool install magi`，也可以直接用
> `magi cli ...`，把 `HOST_WORKSPACE_DIR` 通过 `--data-dir` 传入。

`install.sh` 仅做三件事：

1. 确认 `magi` 在 PATH 上（缺失时会用 `uv tool install` 装一份）；
2. 建好数据根目录（带 `MAGIC/`, `MAGIS/` 两个子目录）；
3. 打印一段 cheat sheet。

它**不会**主动启动 MAGI、**不会**注册服务。

## 目录布局

启动后数据根会长成这样（与 K8s PVC 完全一致）：

```text
~/.magi/                                       # Linux
~/Documents/.magi/                             # macOS / Windows
├── MAGIC/                                     # 私有 MAGI 工作区
│   ├── eva-000/workspace/
│   │   ├── SOUL.md
│   │   ├── memories/
│   │   │   ├── magi.db                        # 私有 SQLite（contacts/sessions/...）
│   │   │   └── sessions/
│   │   ├── skills/
│   │   ├── logs/
│   │   └── tmp/
│   └── eva-001/workspace/                     # 第二个 MAGI
│       └── ...                                # （结构同上）
└── MAGIS/                                     # 每个 MAGIS 一个目录
    └── genesis-01/
        ├── magis.db                           # 组织架构 + 控制面 SQLite
        ├── control-secret                     # 0600，内部 API HMAC 密钥
        └── launcher.json                      # launcher 状态快照
```

控制面状态（runtime 注册、端口分配、workspace 归档、HMAC 密钥）全部在
MAGIS SQLite 中，与 K8s 的 `eva_runtimes` + `control_settings` 一致。
不再有独立的 `control/local-registry.db`。

每个 MAGI 的 workspace 落在 `~/.magi/MAGIC/<slug>/workspace/`，
与 K8s 里 PVC 的 `<workspace>/memories/magi.db` 命名规则完全一致。
MAGIS 格式为 `MAGIS/<magis_id>-<slug>/magis.db`。

## CLI 命令

```bash
magi cli start                # 启动 Adam（前台 + 浏览器），使用 exec 替换当前进程
magi cli start --name eva-001  # 启动指定 MAGI
magi cli start --port 42070   # 指定端口
magi cli start --no-open      # 不打开浏览器
magi cli status               # 列出所有 MAGIC slots 及其状态
magi cli stop                 # 向所有 MAGI runtime 发送 SIGTERM
magi cli doctor               # 诊断打印（路径、DB 状态）
magi cli install-service      # 为每个 MAGI 注册独立 systemd 单元（Linux only）
magi cli uninstall-service    # 移除所有 magi-*.service 单元（Linux only）
```

所有命令都接受 `--data-dir <path>` 覆盖默认数据根，等价于
`HOST_WORKSPACE_DIR=<path>`。

## 服务注册（Linux）

`magi cli install-service` 扫描 `MAGIC/` 下的所有 slot，为每个 MAGI
生成独立的 systemd 用户单元：

```bash
# 生成并启用：
systemctl --user enable --now magi-adam.service     # port 42069
systemctl --user enable --now magi-eva-00.service   # port 42070
# ...
```

每个单元独立管理——一个 MAGI 崩溃，`Restart=on-failure` 只重启它自己，
不影响其他 MAGI。

管理命令：

```bash
systemctl --user status magi-adam.service
systemctl --user stop magi-eva-00.service
journalctl --user -u magi-adam.service -f
systemctl --user list-units 'magi-*'
```

删除：`magi cli uninstall-service` 会停止并移除所有单元。

## 设计要点

- **每个 MAGI 是独立进程**：`magi cli start` 用 `execve` 替换自身为
  `magi runtime`，当前终端直接拥有 MAGI 进程。systemd 模式下每个 MAGI
  是独立 unit，独立崩溃、独立重启。
- **与 K8s 一致的 `workspace/memories/magi.db`**：K8s Pod 的 SQLite
  在 `<workspace>/memories/magi.db`（`MAGI_WORKSPACE_DIR` 指向的 PVC）；
  本路径保持相同约定 `~/.magi/MAGIC/<slug>/workspace/memories/magi.db`。
  `magi/launcher/paths.py` 是唯一暴露这个布局的位置。
- **路径解析由环境变量驱动**：K8s Pod 设置 `MAGI_WORKSPACE_DIR`；
  本地进程设置 `HOST_WORKSPACE_DIR` + `MAGI_RUNTIME_ID` + `MAGI_NAME`。
  不存在硬编码的 `/workspace` 路径。
- **不依赖 Docker / podman / k8s**：唯一外部依赖是 Python 3.12+。
- **`magi cli start` 首次运行是幂等的**：第一次跑会初始化 SQLite
  schema 并生成 control secret；之后再跑直接启动 runtime。

## 升级

新版本只需 `uv tool install --upgrade magi`（或同等 pip 流程）。
数据保存在 `~/.magi/`，不受 Python 包升级影响。

## 卸载

```bash
magi cli uninstall-service                    # 移除 systemd 单元（Linux）
rm -rf ~/.magi                                # 数据根
uv tool uninstall magi                        # 移除包
```

macOS / Windows 也可同样使用，但需手动删除 `~/Documents/.magi`。
