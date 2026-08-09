# MAGI 单机部署

单机部署采用显式 provisioning 与 lifecycle 命令；运行命令不会隐式创建
数据库、身份或默认文件。

```bash
./deploy/cli/install.sh
./deploy/cli/magi init
./deploy/cli/magi node run                 # 默认后台运行 eva-000
./deploy/cli/magi webui run                # 默认后台运行控制台
```

访问 `http://127.0.0.1:42069`。节点 Runtime 只监听本地端口：`eva-000`
为 42070，新增节点从 42071 起获得持久化分配。

```bash
./deploy/cli/magi node create --name eva-001
./deploy/cli/magi node run --name eva-001
./deploy/cli/magi node status --name eva-001
./deploy/cli/magi node stop --name eva-001
./deploy/cli/magi webui status
```

容器与 systemd 使用前台模式：`magi node run --foreground` 与
`magi webui run --foreground`。

数据布局由 `magi init` / `magi node create` 创建：

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
