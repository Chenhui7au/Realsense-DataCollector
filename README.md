# D435i Capture

浏览器端的数据采集工具，通过 Intel RealSense D435i 分八个阶段录制原始数据，每轮采集落盘为一组 RealSense bag 文件。

采集员用浏览器访问，按引导走完八个阶段。每个阶段先看一张姿态示意图，再进入采集页录制一条。八条录完结束本轮，可以再开新一轮。数据按 `<项目目录>/<会话名称>/` 归档，方便人工整理与交接。

当前进度。前端已完成并实测通过。后端已完成接口一览 2.1 至 2.4 四组，也就是基础、项目目录、目录浏览与示意图，共十三个接口，已对着前端逐字段实测。会话、录制与预览需要相机，尚未实现，这些路径暂时返回 501 与 `NOT_IMPLEMENTED`，代码在 `backend/app/routers/pending.py`。

---

## 环境要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Node.js | 20 以上 | 实测 v24.14.1 |
| npm | 10 以上 | 实测 11.11.0 |
| Python | 3.10 | 实测 conda base 环境，命令行直接用 `python` |
| 采集主机 | Windows 10 或 11 | 官方验证过的平台，装好 RealSense SDK 后即可用，见下面相机一节 |
| 相机 | D435i | 只影响会话、录制、预览三组接口与设备探测，其余十二个接口不需要相机 |

Python 直接用 conda 的 base 环境，不要为本项目建虚拟环境。依赖是 FastAPI、Uvicorn、PyYAML、Pillow 与 python-multipart，base 里都有。

有一处版本约束要注意。`fastapi` 与 `starlette` 必须成对匹配，当前可用组合是 `fastapi 0.141.1` 配 `starlette 1.0.0`。曾在 base 里遇到过 `fastapi 0.116.1` 配 `starlette 1.0.0` 的组合，服务会当场起不来，`pip check` 会报冲突。装依赖后跑一次 `pip check` 确认。

TypeScript 固定在 5.x。`vue-tsc` 3 依赖 `typescript/lib/tsc` 这个子路径，TypeScript 7 移除了它，升级会导致类型检查无法启动。`package.json` 里已经锁好，不要手动升到 7。

---

## 前端

所有命令都在 `frontend/` 下执行。

### 安装依赖

```powershell
cd frontend
npm install
```

### 启动开发服务器

```powershell
cd frontend
npm run dev
```

默认监听 `http://localhost:5173`。采集现场用另一台机器访问时走局域网地址，Vite 已开启 `host: true`，启动日志里会打印 Network 那一行。

### 类型检查

```powershell
cd frontend
npm run typecheck
```

### 生产构建

```powershell
cd frontend
npm run build
```

产出在 `frontend/dist/`，是纯静态文件，由后端的 FastAPI 直接托管。构建命令内部会先跑一次类型检查，类型不过就不会产出产物。

### 本地预览构建产物

```powershell
cd frontend
npm run preview
```

### 从仓库根目录执行

不想先切目录的话，可以用 `--prefix`。

```powershell
npm --prefix frontend run dev
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

---

## 模拟后端与真实后端

前端有两个后端实现，编译期由 `VITE_USE_MOCK` 决定用哪个。用别名切换而不是运行时判断，目的是让模拟代码彻底不进生产包。

| 取值 | 后端 | 用途 |
| --- | --- | --- |
| `true` | `src/api/mock.ts` | 浏览器内模拟，无相机无服务也能跑 |
| `false` | `src/api/real.ts` | 请求 `/api`，由 Vite 代理转发到后端 |

默认值写在环境文件里。

- `frontend/.env.development` 是 `true`，所以 `npm run dev` 默认走模拟
- `frontend/.env.production` 是 `false`，所以 `npm run build` 固定走真实接口

模拟模式下界面右上角会显示一个 `Mock` 徽标。看到它就说明当前没有真实数据，不要让采集员在这种状态下工作。

### 临时切到真实后端

不用改文件，命令行覆盖即可。

```powershell
cd frontend
$env:VITE_USE_MOCK="false"
npm run dev
```

### 永久切到真实后端

改 `frontend/.env.development` 这一行，然后重启开发服务器。

```text
VITE_USE_MOCK=false
```

### 代理配置

Vite 把 `/api` 转发到 `http://127.0.0.1:8000`。后端换了主机或端口，改 `frontend/vite.config.ts` 里的 `server.proxy` 段。

### 模拟模式的限制

模拟后端的状态全在内存里，浏览器整页刷新会清空。于是有两件事没法在模拟模式下验证，需要真实后端才能测。刷新后恢复会话，以及示意图与项目目录设置跨重启保留。

---

## 后端

FastAPI 加 Uvicorn，配置文件是 `backend/config/config.yaml`。接口契约见 `docs/API.md`，模块划分与并发约定也在那一份里。

### 依赖

base 里已有。确认一下没缺就是这两条命令。

```powershell
python -c "import fastapi, uvicorn, yaml, PIL, multipart; print('ok')"
python -m pip check
```

缺的话装进 base，不要建虚拟环境。

```powershell
python -m pip install -r backend\requirements.txt
```

### 启动

用 `backend/run.py`，它会按配置文件里的地址与端口起服务，默认 `127.0.0.1:8000`。

```powershell
python backend\run.py
```

默认带文件监听，改代码自动重启。在采集现场不要监听，重启会打断引导页。

```powershell
python backend\run.py --no-reload
```

换配置文件或覆盖地址。

```powershell
python backend\run.py --config C:\path\to\config.yaml --port 8100
```

启动前先用 `python -V` 确认它指向 conda base，`where python` 能看出实际用的是哪一个。

### 接口文档

服务起来后访问 `http://127.0.0.1:8000/docs`，拿到的就是 `backend/app/models.py` 里的响应模型生成的文档，与 `docs/API.md` 一一对应。

### 跑测试

```powershell
python -m pytest backend\tests -q
```

测试不碰真实服务目录，也不需要相机。每个用例在临时目录里现造一份配置。

### 服务自己的数据

配置里 `paths` 下的路径相对于配置文件所在的目录解析，不是相对于当前工作目录，所以从哪个终端启动都指向同一份数据。默认值写的是 `../var`，落在 `backend/var/`。

```text
backend/var/
  settings.json        采集员在界面上改的项目目录，优先级高于 YAML 的 default_root
  settings.json.bak
  guides/
    manifest.json      示意图清单与阶段描述，唯一的索引
    manifest.json.bak
    logs/app.log
```

`backend/var/` 不进版本库。它记的是本机路径与本机素材，换台机器重配就行。

### 日常开发两个终端

```powershell
# 终端一，后端
python backend\run.py

# 终端二，前端，切到真实接口
$env:VITE_USE_MOCK="false"; npm --prefix frontend run dev
```

### 还没实现的部分

会话、录制、预览三组接口需要相机，也依赖会话状态机，尚未实现。这十四条路径现在返回 501 与 `NOT_IMPLEMENTED`，前端会把它当成可读的提示而不是未知错误。

返回 501 的路径集中在 `backend/app/routers/pending.py`，一条路由一行。实现一组就删一行，没有别处引用这个模块。

- 相机探测在 `backend/app/device.py`。它不直接调 SDK，而是把枚举隔到一个子进程 `backend/tools/enumerate_devices.py` 里，超时与段错误都关在子进程内，不会把服务打死。
  这不是多此一举。SDK 打开失败或者设备被别的进程占着时，收尾阶段可能直接段错误，进程以 139 退出，`try` 抓不住，卡住的 native 调用也拦不住信号。只有子进程能同时关住这两种情况。
  已实测。连打四次健康检查，子进程崩了四次，服务始终返回 200 并给出可读原因。
- 探测默认开启，会真实打开设备。持续开发前端或者跑测试时把它关掉，服务就完全不碰相机。

```yaml
camera:
  probe: false
```

测试套件自己就是关着的，所以不需要相机也不会变慢。想手工看一次相机状态，用这个脚本，它按步骤打印并在卡住时报出卡在哪一步。

```powershell
python backend\tools\probe_camera.py
# 连每个流的分辨率与帧率一起列
python backend\tools\probe_camera.py --profiles
```

连不上、需要逐步定位时用诊断脚本 `backend/tools/diagnose_access.py`，`--no-kill` 可以跳过占用进程清理，直接看基线失败。

### 采集主机

采集主机只支持 **Windows 10 与 11**。它也是官方验证过的平台之一，原文见 `doc/support-matrix.md`。

安装方式只有一条。下载 `RealSense.SDK.exe`，一路点下一步，装完插上相机就能用。插上 USB3 口后能在设备管理器里看到相机，RealSense Viewer 也可以直接打开，没有额外的权限或规则要配。

一条必须遵守的约束。**不要装在虚拟机里**，官方文档写明不支持，原因是 USB 3.0 在虚拟机里有转换层，真要试他们只推荐 VMware 而不是 VirtualBox。

Linux 与 macOS 不在本项目的目标平台内。官方也支持 Ubuntu，但本项目的部署、脚本与文档都以 Windows 为准，未在 Windows 上验证过的路径不保证可用。

主机侧与交换机无关的一条是 USB 链路速率，见上一节。

### 与操作系统无关的一条

USB 链路速率由硬件与线材决定。本机这台 D435i 实测只有 **480 Mbps，即 USB 2.0**，换过接口仍是这个速率，服务发现不是 3.x 会在日志里警告，首页的 Link speed 也会显示出来。
D435i 在 USB 2.0 下深度分辨率与帧率都会缩水。这条换机器不会自动变好，得换 USB 3 数据线与不共享带宽的接口。采集质量受它的影响比权限问题更直接。

---

## 数据落盘

两层结构，项目目录由采集员在主页设置，会话名称在新建时指定。

```text
C:\Users\ch7au\Documents\Project_A\   <- 主页配置的项目目录
  session_a\                          <- 新建会话时命名
    session.json
    stage_01\capture.bag
    stage_01\meta.json
    stage_01\thumb.jpg
    stage_08\...
  session_b\
    ...
```

项目目录里除了会话子目录没有别的东西，整个目录拷走就是一份干净的数据集。服务自己的文件，也就是示意图、日志与设置，放在另一个服务目录里，两者不要混。

---

## 目录结构

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
      services.py        容器与启动自检顺序
      routers/           一组接口一个模块
    tests/test_api.py    十二个接口的测试，不需要相机
    var/                 运行时数据，不进版本库
  frontend/
    src/
      api/               数据模型与两个后端实现
      components/        通用组件
      composables/       状态层
      router/            路由与守卫
      styles/            设计令牌与基础样式
      utils/             格式化与校验
      views/             五个页面
    dist/                构建产物，不进版本库
```

---

## 文档

改代码前先看对应的一份，三份的分工是明确的。

| 文档 | 什么时候看 |
| --- | --- |
| `docs/DESIGN.md` | 想搞清楚为什么这么设计，需要改需求、配置、落盘结构或相机参数时 |
| `docs/FRONTEND.md` | 改界面、改交互、加页面、调整样式时 |
| `docs/API.md` | 加接口、改字段、写后端实现时 |

接口改动有一处顺序要遵守。先改 `docs/API.md`，再同步 `frontend/src/api/types.ts`，最后改页面。反过来做一定会出现文档与代码不一致。

---

## 常见问题

**`npm run dev` 报找不到 package.json**

当前目录不对。`package.json` 在 `frontend/` 下，先切进去，或者从仓库根目录用 `npm --prefix frontend run dev`。

**端口被占用**

换端口启动，`npx vite --port 5174`。注意这只改了前端端口，`/api` 仍然代理到 8000。

**界面右上角一直显示 Mock**

说明跑在模拟后端上，`VITE_USE_MOCK` 还是 `true`。按上面那节切到 `false` 并重启开发服务器。

**页面数据看起来是假的**

模拟后端就是这么设计的，示意图是程序生成的 SVG，相机画面是 canvas 画的。接上真实后端后这些都会换成真数据。

**改了 `.env.development` 没生效**

环境文件只在开发服务器启动时读一次，改完要重启 `npm run dev`。

**类型检查报 `./lib/tsc` 相关错误**

TypeScript 被升到了 7。降回 5，`npm install -D typescript@5`。

**后端起不来，报 `Router.__init__() got an unexpected keyword argument 'on_startup'`**

`fastapi` 与 `starlette` 版本不匹配，不是代码问题。跑 `python -m pip check` 看冲突，然后 `pip install "fastapi==0.141.1"`。

**后端起不来，报 `config file not found`**

`--config` 的路径不对，或者配置文件不在 `backend/config/config.yaml`。默认值就是那一份，从仓库任意位置启动都可以。

**改了阶段名称或时长但界面没变**

阶段表只在启动时读一次，改完 YAML 要重启服务。它们刻意不放在界面上编辑，因为这些文案属于采集流程定义，改动应当留下痕迹。

**首页说相机未检测到**

这是当前预期状态。主机上没装 SDK，或者没插相机，或者插了但打不开。原因那句话由后端给出，直接读它就行，不用去猜。
常见的有两类，缺 SDK 与设备打不开。

**首页说没有项目目录**

首次启动的正常状态，在主页指定一个即可。也可以先在 YAML 的 `project.default_root` 写死一个默认值，仅在没有 `settings.json` 时才生效。

**在主页选了 `D:\` 这类盘符根目录被拒**

盘符根目录不允许当作项目目录，往下一级选一个具体目录，例如 `D:\Project_A`。

**示意图上传被拒，说文件是别的格式**

服务按文件真实内容判断格式，不看扩展名。PNG、JPEG、WebP、BMP 与 GIF 都接受，TIFF 与 SVG 不接受，把 TIFF 改名成 `.png` 会被拒，报 `GUIDE_UNSUPPORTED_TYPE`。反过来，声明类型写错但真实格式在列表内的文件会被正常存下，存的是它真实的格式。

**阶段描述保存不了**

先看输入框右下角的字数，超过 500 会转红并禁用保存，这时后端也会拒绝并报 `GUIDE_TEXT_TOO_LONG`。想恢复配置文件里的默认文案，点 Reset 清空即可。
