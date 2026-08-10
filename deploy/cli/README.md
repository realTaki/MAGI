# MAGI 单机部署

第一次使用只需执行安装脚本；它会安装 MAGI，并自动完成 Genesis provisioning、
第一个 MAGI 与 WebUI 的启动：

```bash
./deploy/cli/install.sh
```

访问 `http://127.0.0.1:42069`。之后使用 `magi start` 即可安全地恢复未运行的
本地服务；首次运行时它才会创建 Genesis 与 `eva-000`，不会覆盖已有状态。
节点 Runtime 只监听本地端口：`eva-000` 为 42070，新增节点从 42071 起获得持久化分配。

需要显式控制生命周期或用于服务管理器时，仍可以使用底层命令：

```bash
./deploy/cli/magi init                      # 只 provision，不启动进程
./deploy/cli/magi node run                  # 默认后台运行 eva-000
./deploy/cli/magi webui run                 # 默认后台运行控制台
```

```bash
./deploy/cli/magi node create --name eva-001
./deploy/cli/magi node run --name eva-001
./deploy/cli/magi node status --name eva-001
./deploy/cli/magi node stop --name eva-001
./deploy/cli/magi webui status
```

容器与 systemd 使用前台模式：`magi node run --foreground` 与
`magi webui run --foreground`。

数据布局由首次 `magi start`（或显式 `magi init`）/ `magi node create` 创建：

```text
~/.magi/
├── MAGI_Citizens/<name>/
│   ├── memories/magi.db
│   ├── runtime.json
│   ├── SOUL.md
│   ├── logs/
│   └── run/
└── MAGI_Societies/genesis/
    ├── magis.db
    └── control-secret
```

这是一次干净切换：如果节点目录里存在旧的 `<workspace>/magi.db`，命令会
拒绝运行。清理状态后重新执行 `magi init`，不会读取、迁移或双写旧数据库。
