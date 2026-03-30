# Agent OS — 运营控制台（`/openclaw/`）使用步骤

主站 `https://你的域名/` 是 **聊天终端**；**类 OpenClaw 布局的多面板控制台** 在单独路径下，并且已通过脚本换标为 **Agent OS**（标题、侧边品牌字、图标），避免与 OpenClaw 官方视觉混淆。

---

## 一、用浏览器打开

1. 在地址栏输入：  
   `https://你的域名/openclaw/`  
   （末尾斜杠建议保留。）
2. 若显示「连接 / 登录」类网关界面：你需要配置 **WebSocket URL** 与 **Token**（见下一节）。

---

## 二、网关连接（必做）

控制台通过 **WebSocket** 与本机 Agent OS 通信，路径与页面一致：

- **WebSocket URL 示例**：`wss://你的域名/openclaw`  
  - 本地开发一般是：`ws://127.0.0.1:8000/openclaw`
- **Gateway Token**：与服务端环境变量 `OPENCLAW_GATEWAY_TOKEN` **完全一致**。  
  - 未设置该变量时，仅允许从本机（loopback）连接；公网访问 **必须** 设置 Token，并在此处填写相同值。

填写后点击界面上的 **Connect / 连接**（或同类按钮）。成功后会进入侧边栏多 Tab 的桌面台。

---

## 三、推荐演示顺序（给甲方看）

1. **Overview**：网关状态、通道摘要（与各 Tab 占位/兼容能力有关）。  
2. **Channels**：可看到 WhatsApp 等通道状态（数据来自当前 Agent OS 配置与桥接心跳）。  
3. **Chat**：在控制台里发一条消息，走兼容网关的 `chat.send`（需后端 LLM 等已配置）。  
4. **Skills / Config / Logs** 等：部分为只读或占位；本仓库兼容层 **不会** 把「在线写回 OpenClaw 式配置」全部打通，以界面不报错、关键信息可读为主。

---

## 四、与主站聊天的区别

| 入口 | 用途 |
|------|------|
| `/` | Agent OS 自研终端：侧边会话列表 + 记忆网络视图，走 HTTP API。 |
| `/openclaw/` | 运营控制台：多 Tab、通道/用量/日志等，走 **WebSocket `/openclaw`**。 |

两者可同时使用；向甲方说明：日常对话可用 `/`，运维与通道总览可打开 `/openclaw/`。

---

## 五、重新构建上游 UI 之后（开发者）

从 OpenClaw 源码构建并拷贝静态文件后，**务必**再跑一次品牌化（已接入构建脚本）：

```bash
./scripts/build_openclaw_control_ui.sh
```

或仅对已存在的静态目录执行：

```bash
python3 scripts/rebrand_openclaw_static.py
```

否则会恢复上游默认图标与「OpenClaw」露出文案。品牌化脚本**不会**改写 `includeInOpenClawGroup` 等内部字段，避免运行时报错。

---

## 六、常见故障

- **连不上 WebSocket**：反代（Caddy/Nginx）需放行 **WebSocket** 到同一路径；Token 不一致或仅允许 loopback 时会拒绝。  
- **页面 404**：确认生产镜像里包含 `my_agent_os/api_gateway/static/openclaw/` 且网关进程挂载正常。  
- **Tab 无数据或报错**：属预期内的兼容范围；需要真能力时可在 `openclaw_compat` 中逐项扩展 RPC。
