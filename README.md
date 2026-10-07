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
  README.md              本文件
  .gitignore
  docs/
    DESIGN.md            需求、架构、配置、数据落盘、相机设计、风险、里程碑
    FRONTEND.md          页面逻辑、交互规则、状态管理、样式约定、运行方式
    API.md               接口契约、数据模型、错误码、持久化、后端实现约定
  backend/
    run.py               开发入口，起服务与传配置
    requirements.txt     依赖清单与版本说明
    config/config.yaml   部署配置，阶段表与相机参数都在这里
    app/
      main.py            应用装配、启动顺序、异常处理器、托管前端产物
      config.py          YAML 解析与启动校验
      errors.py          统一错误体与三个全局处理器
      models.py          响应模型，与 API.md 第三节一一对应
      paths.py           路径规范化与名称规则
      settings.py        settings.json 的原子读写
      project.py         项目目录的校验与探测
      fsbrowser.py       目录浏览与新建目录
      guides.py          示意图清单、启动自检、展示副本
      device.py          相机探测，未接相机时给出原因
      camera.py          采集线程、pipeline 计划、JPEG 编码
      sessions.py        会话目录布局、清单读写、中断恢复
      capture.py         会话状态机，把相机与磁盘串起来
      diagrams.py        默认示意图的绘制，几何与渲染分开以便断言
      services.py        容器与启动自检顺序
      routers/           一组接口一个模块
    tests/               测试，不需要相机，也不需要真实服务目录
    tools/               相机探测、占用排查与设备枚举脚本
    var/                 运行时数据，不进版本库
  frontend/
    src/
      api/               数据模型与 HTTP 客户端
      components/        通用组件
      composables/       状态层
      router/            路由与守卫
      styles/            设计令牌与基础样式
      utils/             格式化与校验
      views/             五个页面
    dist/                构建产物，不进版本库
```

服务自己的运行数据落在 `backend/var/`，也就是示意图、日志与设置文件，两者不要混。采集数据落在主页配置的项目目录下，形如 `<项目目录>/<会话名称>/`，整个项目目录拷走就是一份干净的数据集。

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

