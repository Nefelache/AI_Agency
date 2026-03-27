# Agent OS — 压力测试（服务器可复现）

本目录随仓库提交到 Git；在任意机器 `git pull` 后即可对**已启动的 API** 做有界并发压测。脚本仅使用 **Python 标准库**，不依赖额外 pip 包。

## 快速开始

1. **安装依赖**（与主项目相同）：

   ```bash
   cd /path/to/Codefile
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **配置并启动网关**（与线上一致），例如：

   ```bash
   # 编辑 my_agent_os/config/.env（可从 config/.env.example 复制）
   uvicorn my_agent_os.api_gateway.main:app --host 0.0.0.0 --port 8000
   ```

3. **在另一终端执行压测**（在仓库根目录）：

   ```bash
   ./stress/run.sh
   ```

   若已配置与服务器相同的 Owner Key：

   ```bash
   export API_KEY_OWNER="与 .env 中 API_KEY_OWNER 一致"
   ./stress/run.sh
   ```

## 输出

结果写入 `stress/results/`（可通过 `STRESS_RESULTS_DIR` 改写）：

| 文件 | 说明 |
|------|------|
| `stress_results.json` | 机器可读 |
| `stress_report.md` | 人类可读摘要表 |

建议将 `stress/results/` 加入 `.gitignore`（已忽略），避免把服务器数据误提交。

## 环境变量

见 [`env.example`](env.example)。常用项：

- **`BASE_URL`** — 默认 `http://127.0.0.1:8000`；云上可设为 `https://你的域名`。
- **`API_KEY_OWNER`** — 与网关 `.env` 中一致时，会额外压测 `/memory/*`、`GET /health/extended`；不设则跳过这些阶段。
- **`STRESS_TOTAL_REQUESTS`** — 每个并行阶段的总请求数（默认 2000）。
- **`STRESS_CONCURRENCY`** — 线程数（默认 20）。
- **`STRESS_AUTH_REGISTER_COUNT`** — 顺序 `POST /auth/register` 次数（默认 20）；设为 `0` 可跳过写库注册压测。
- **`STRESS_FAIL_ON_ERROR`** — 默认 `1`：任一阶段有失败则进程退出码 **2**；设为 `0` 仅统计不失败退出。
- **`STRESS_RESULTS_DIR`** — 结果目录。

## 命令行参数（覆盖环境）

```text
python3 stress/stress_test.py --help
```

示例：快速冒烟（请求数少、便于 CI）：

```bash
./stress/run.sh --requests 100 --workers 8 --register-count 5
```

## 压测阶段说明

| 阶段 | 说明 |
|------|------|
| GET `/health`, `/billing/plans`, `/`, `/setup` | 公开读路径，始终执行 |
| GET `/health/extended` | 需 `API_KEY_OWNER` |
| GET `/memory/stats`, `/memory/list`, `/memory/sessions` | 需 `API_KEY_OWNER` |
| POST `/memory/search` | JSON 检索，需 `API_KEY_OWNER` |
| POST `/auth/register` | 顺序执行，唯一邮箱，压 SQLite 用户写入 |

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功；若 `STRESS_FAIL_ON_ERROR=1`，所有已执行阶段无错误 |
| 1 | 无法连接或 `/health` 非 200 |
| 2 | 至少一个已执行阶段存在失败（可用 `--allow-errors` 或 `STRESS_FAIL_ON_ERROR=0` 关闭） |

## 与旧路径的兼容

历史上脚本位于 `my_agent_os/tests/stress_test.py`，现已改为调用本目录实现，行为以 `stress/stress_test.py` 为准。
