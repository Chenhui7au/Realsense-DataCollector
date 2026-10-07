# RealSense Data-Collector

浏览器端的数据采集工具，通过 Intel RealSense D435i 分八个阶段录制原始数据，每轮采集落盘为一组 RealSense bag 文件。

---

## 环境要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Node.js | 20 以上 | 实测 v24.21.0 |
| npm | 10 以上 | 实测 11.19.0 |
| Python | 3.12 以上 | 实测 conda base 环境 Python 3.14.7，命令行直接用 `python` |
| 采集主机 | Windows 10 或 11 | 官方验证过的平台，装好 RealSense SDK 后即可用，不要装在虚拟机里 |
| 相机 | D435i | 只有会话、录制、预览三组接口与设备探测需要相机，其余接口在无相机时也全部可用 |

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

