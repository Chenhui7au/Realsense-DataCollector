# RealSense Data-Collector

浏览器端的数据采集工具，通过 Intel RealSense D435i 分阶段录制原始数据，每轮采集落盘为一组 RealSense bag 文件。阶段数可配置，默认八个。

---

## 环境要求

| 项 | 要求 |
| --- | --- |
| Node.js | 20 或以上 |
| npm | 10 或以上 |
| Python | 3.12 或以上 |
| 采集主机 | Windows 10 或以上 |
| 相机 | D435i |

---

## 项目结构

```text
Realsense/
  docs/       # 项目设计文档
  backend/    # 后端服务
  frontend/   # 前端页面
```

---

## 使用方法

后端与前端都要，开发时开两个终端。以下命令都可以从仓库根目录执行。

### 装依赖

```powershell
python -m pip install -r backend\requirements.txt   # conda base 里通常已有
npm --prefix frontend install
```

### 基础配置

采集参数都在 [backend/config/config.yaml](backend/config/config.yaml) 里，改完重启服务生效。常改的几项：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| `stages_count` | 一轮的阶段数 | `8`，可填 1 到 10 |
| `camera.recording.min_duration_s` | 低于该时长的录制会被丢弃，秒 | `1` |
| `stages[].max_duration_s` | 单个阶段的最长录制时长，秒 | `300` |

### 启动后端

```powershell
python backend\run.py
```

默认 `127.0.0.1:8000`，带文件监听，改代码自动重启。起好后接口文档在 `http://127.0.0.1:8000/docs`。

```powershell
python backend\run.py --no-reload                      # 采集现场用，重启会打断引导页
python backend\run.py --port 8100                      # 换端口
python backend\run.py --config C:\path\to\config.yaml  # 换配置文件
```

### 启动前端

`npm run dev` 需要后端已经在跑，Vite 把 `/api` 转发到 `http://127.0.0.1:8000`。

```powershell
npm --prefix frontend run dev
```

默认监听 `http://localhost:5173`，已开启 `host: true`，采集现场用另一台机器走局域网地址即可。后端换了主机或端口，改 `frontend/vite.config.ts` 里的 `server.proxy`。

### 类型检查与构建

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run build      # 先类型检查，再产出 frontend/dist/
npm --prefix frontend run preview    # 本地预览构建产物
```

构建产物是纯静态文件，由后端直接托管。生产部署只要先 `npm run build`，再起后端一个进程，不需要 Vite。

### 跑测试

```powershell
python -m pytest backend\tests -q
```

测试不碰真实服务目录，也不需要相机，每个用例在临时目录里现造一份配置。

