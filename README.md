# 贾维斯语音助手 — 云端大脑服务

中文语音助手，Android + PC + Web 三端。本地唤醒词（"贾维斯"）→ 流式语音交互，同一云端大脑处理理解与执行，会话跨设备共享。

**版本：v0.5.0** | 全部里程碑完成

## 开发状态

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| M1.1 brain 骨架 | ✅ | FastAPI + WS + token 鉴权 |
| M1.2 STT 集成 | ✅ | sherpa-onnx Paraformer + Silero VAD，本地离线 |
| M1.3 LLM + Tool Calling | ✅ | mimo-v2.5 流式 + 按句切分 + 14 个工具 |
| M1.4 TTS 集成 | ✅ | mimo-v2.5-tts HTTP 流式 |
| M1.5 PC 客户端 | ✅ | 按键/唤醒词录音 → TTS 播放 + 粒子窗口 |
| M1.6 部署 VPS | 🟡 | 配置就绪（Docker/systemd/nginx），待实机部署 |
| M2 唤醒词接入 | ✅ | Porcupine（Android + PC），支持降级模式 |
| M3 双端同步 | ✅ | 共享会话历史 + 话轮仲裁 + 设备列表 |
| M4 打磨 | ✅ | 断线重连 + 能量过滤 + 插件系统 + 取消支持 |
| 插件系统 | ✅ | 智能家居/媒体/应用控制/Webhook |
| 粒子 UI | ✅ | 三端粒子可视化（Web/PC/Android） |

## 待完成

| 项目 | 优先级 | 说明 |
|------|--------|------|
| VPS 实机部署 | P0 | 需要境外 VPS + 域名，测试跨境延迟 |
| Android 真机测试 | P0 | 需要 Porcupine AccessKey + .ppn 文件 |
| TTS PCM 格式验证 | P1 | mimo-v2.5-tts 输出格式需与客户端对齐 |
| 写操作工具 | P2 | 提醒/日历等写操作需语音二次确认 |
| 多语言支持 | P3 | 英文/方言识别 |
| iOS 客户端 | P3 | v2 规划 |

## 技术架构

```
[Web / PC / Android 客户端]
        │ wss://
        ▼
┌────────────────────────────────┐
│    brain 服务 (Python) v0.5.0   │
│    FastAPI + WebSocket          │
│    ┌──────────────────────────┐│
│    │ STT: sherpa-onnx         ││  本地离线
│    │ LLM: mimo-v2.5           ││  OpenAI 兼容流式
│    │ TTS: mimo-v2.5-tts       ││  HTTP 流式
│    │ Tools: 14 个工具          ││  插件系统
│    └──────────────────────────┘│
│    ┌──────────────────────────┐│
│    │ 共享会话历史 + 取消支持    ││  M3/M4
│    │ 断线重连 + 保活 + 限流    ││
│    └──────────────────────────┘│
└────────────────────────────────┘
```

## 核心特性

- **流式管线** — STT → LLM → TTS 全链路流式
- **Tool Calling** — 14 个工具（天气/计算/日期/搜索/智能家居/媒体/应用/Webhook）
- **插件系统** — 自动发现，`plugins/custom/` 放 .py 即可
- **对话历史** — 共享 20 条消息，支持多轮上下文
- **取消支持** — 任意阶段可取消当前轮次
- **断线重连** — 指数退避（1s → 30s），重连后自动补发上下文
- **能量过滤** — 静音音频不上行
- **粒子可视化** — 三端统一粒子效果，随语音起伏

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 下载 STT 模型
python download_models.py

# 3. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY

# 4. 启动服务
python server.py        # 或双击 start.bat

# 5. 打开浏览器
# http://localhost:8000/
```

## 客户端

| 客户端 | 启动方式 | 特性 |
|--------|----------|------|
| **Web** | 浏览器访问 `http://server:8000/` | 粒子可视化 + 语音交互 |
| **PC** | `python pc_client.py --particles` | 粒子窗口 + 唤醒词 |
| **Android** | 安装 APK | 粒子背景 + 前台服务 |

## 工具集

| 工具 | 功能 | 来源 |
|------|------|------|
| `get_weather` | 天气查询 | tools.py |
| `calculate` | 数学计算 | tools.py |
| `get_datetime` | 日期时间 | tools.py |
| `search_web` | 网络搜索 | tools.py |
| `smart_home_control` | 智能家居控制 | plugins/smart_home.py |
| `media_control` | 媒体播放控制 | plugins/media.py |
| `open_app` | 应用启动 | plugins/app_control.py |
| `webhook_trigger` | HTTP API 触发 | plugins/webhook.py |
| `send_notification` | 推送通知 | plugins/webhook.py |

## 部署

```bash
# Docker
docker-compose up -d

# VPS 一键部署
bash deploy.sh

# systemd
cp jarvis-brain.service /etc/systemd/system/
systemctl enable --now jarvis-brain
```

## 技术栈

| 模块 | 选型 |
|------|------|
| STT | sherpa-onnx + Paraformer CN-small + Silero VAD（本地离线） |
| LLM | mimo-v2.5（OpenAI 兼容，流式 + Tool Calling） |
| TTS | mimo-v2.5-tts（OpenAI 兼容，HTTP 流式） |
| 通信 | FastAPI + WebSocket (wss) + 30s keepalive |
| 客户端 | PC: Python / Android: Kotlin / Web: HTML5 Canvas |
| 部署 | Docker / systemd / nginx + Let's Encrypt |

## 项目结构

```
jarvis-brain/
├── server.py          # 主服务：WS 端点 + STT→LLM→TTS 管线
├── stt.py             # STT 引擎：sherpa-onnx + Silero VAD
├── llm.py             # LLM 引擎：mimo-v2.5 流式 + Tool Calling
├── tts.py             # TTS 引擎：mimo-v2.5-tts HTTP 流式
├── tools.py           # 内置工具（天气/计算/日期/搜索）
├── plugins/           # 插件系统
│   ├── smart_home.py  # 智能家居（Home Assistant/Hue/MQTT）
│   ├── media.py       # 媒体控制（音量/播放/Spotify）
│   ├── app_control.py # 应用控制（PC/Android）
│   ├── webhook.py     # HTTP 触发（IFTTT/Bark/通知）
│   ├── devices.json   # 设备注册表
│   └── custom/        # 自定义插件目录
├── config.py          # 启动配置校验
├── structured_logging.py  # 结构化日志
├── particle_window.py # PC 粒子窗口
├── wake_word.py       # PC 唤醒词检测
├── pc_client.py       # PC 客户端
├── static/
│   └── index.html     # Web 客户端（粒子可视化）
├── android/           # Android 客户端（Kotlin）
├── download_models.py # STT 模型下载
├── start.bat          # Windows 一键启动
├── deploy.sh          # VPS 部署脚本
├── Dockerfile         # Docker 镜像
├── docker-compose.yml # Docker Compose
├── requirements.txt   # Python 依赖
├── .env.example       # 环境变量模板
└── jarvis-assistant-spec-v0_3.md  # 技术规格文档
```
