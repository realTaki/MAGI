# MAGI 单机本地部署（非容器，openclaw 风格）

这是 MAGI 提供的**单机、非容器**部署方式。意图是：

- 一个 `magi` 命令就能拉起 ADAM 与 Genesis MAGIS；
- 持久化数据放在一个明显的目录里，方便备份、复制、迁移；
- 仅依赖系统已有的 Python / systemd，不引入 Docker / k8s；
- 可以选择以 `magi local install-service` 把 MAGI 注册为登录自启
  的用户服务，不需要 root 权限。

这条路径适合：单机开发者、本地评审、单机 PoC、小团队自托管。

| 平台 | 数据根 | 类型 |
| --- | --- | --- |
| Linux | `~/.magi/` | 隐藏目录 |
| macOS | `~/Documents/.magi/` | 放在 Documents 下 |
| Windows | `%USERPROFILE%\Documents\.magi\` | 放在 Documents 下 |

`$MAGI_DATA_ROOT` 始终优先于上述默认路径。

## 快速开始

```bash
# 1. 安装
./deploy/local/install.sh

# 2. 启动（默认前台 + 打开浏览器）
./deploy/local/magi local start

# 3. （可选）注册 systemd user unit，让 MAGI 登录后自动启动
./deploy/local/magi local install-service

# 4. 卸载服务
./deploy/local/magi local uninstall-service
```

`install.sh` 仅做三件事：

1. 确认 `magi` 在 PATH 上（缺失时会用 `uv tool install` 装一份）；
2. 建好数据根目录（带 `control/`, `MAGIC/`, `MAGIS/local/` 三个子目录）；
3. 打印一段 cheat sheet。

它**不会**主动启动 MAGI、**不会**注册服务。

## 目录布局

启动后数据根会长成这样（与生产 PVC 完全一致）：

```text
~/.magi/                                       # Linux
~/Documents/.magi/                             # macOS / Windows
├── control/                                   # launchpad-only state
│   ├── local-registry.db                      # SQLite: 启动的 runtime / 端口 / PID
│   ├── control-secret                         # 0600，随机生成的 HMAC 密钥
│   └── launcher.json
├── MAGIC/                                     # 私有 MAGI 工作区
│   └── 1-adam/workspace/
│       ├── SOUL.md
│       ├── memories/
│       │   ├── magi.db
│       │   └── sessions/
│       ├── skills/
│       ├── logs/
│       └── tmp/
└── MAGIS/local/                               # 直属 MAGIS（Genesis）数据库
    └── magis.db
```

每个 EVA 由 `magi local start` 引导 ADAM 之后，按 MAGIS 树创建。它们的
工作区就落在 `~/.magi/MAGIC/<id>-<slug>/workspace/`，与生产里
的 PVC 命名规则一致。

## CLI 命令

```bash
magi local start              # 启动 ADAM（前台 + 浏览器）
magi local start --no-open    # 不打开浏览器
magi local start --print-secret  # 打印控制面 secret
magi local status             # 列出已注册的 runtime
magi local stop               # 停止所有 runtime（不释放端口）
magi local doctor             # 诊断打印
magi local install-service    # 注册 systemd user unit（Linux only）
magi local uninstall-service  # 移除 systemd user unit（Linux only）
```

所有命令都接受 `--data-dir <path>` 覆盖默认数据根，等价于
`MAGI_DATA_ROOT=<path>`。

## 服务注册（Linux）

`magi local install-service` 把 `deploy/local/service/magi.service`
复制到 `~/.config/systemd/user/magi.service`，并把 `__MAGI_BIN__`
替换成本机 `magi` 真实路径，然后执行：

```bash
systemctl --user daemon-reload
systemctl --user enable --now magi.service
```

注册后：

```bash
systemctl --user status magi.service
systemctl --user stop magi.service
journalctl --user -u magi.service -f
```

删除：`magi local uninstall-service`。

## 设计要点

- **不动 `/workspace`**：容器化的生产 k8s 把 `/workspace` 挂成
  PVC；本路径只是把同样的目录树放在 `~/.magi/MAGIC/<id>-<slug>/workspace/`
  下。`magi/launcher/paths.py` 是唯一暴露这个布局的位置。
- **不复用 `MAGI_WORKSPACE_DIR`**：那个变量专属于 BUS 容器化
  路径；本地路径必须用 `MAGI_DATA_ROOT` 切换——这是有意为之，
  避免任何模块偷偷把宿主目录穿到容器抽象里。
- **不依赖 Docker / podman / k8s**：唯一外部依赖是 Python 3.12+。
  这与 openclaw 的"单个可执行"思想一致。
- **0-arg `magi local start` 是幂等的**：第一次跑会初始化 SQLite
  schema 并生成控制 secret；之后再跑只是检测到 ADAM 已注册后
  保活。

## 升级

新版本只需 `uv tool install --upgrade magi`（或同等 pip 流程）。
数据保存在 `~/.magi/`，不受 Python 包升级影响。

## 卸载

```bash
magi local uninstall-service                                  # 移除 systemd 单元（Linux）
rm -rf ~/.magi                                                # 数据根
uv tool uninstall magi                                        # 移除包
```

macOS / Windows 也可同样使用，但需手动删除 `~/Documents/.magi`。
