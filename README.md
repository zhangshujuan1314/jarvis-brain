# 贾维斯语音助手 — 云端大脑服务

中文语音助手，Android + PC 双端。本地唤醒词（"贾维斯"）→ 流式语音交互，同一云端大脑处理理解与执行，会话跨设备共享。

**版本：v0.4.0** | M1.1–M1.5 + M4 全部完成

## 开发状态

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| M1.1 brain 骨架 | ✅ | FastAPI + WS + token 鉴权 |
| M1.2 STT 集成 | ✅ | sherpa-onnx Paraformer CN-small + Silero VAD，本地离线 |
| M1.3 LLM + Tool Calling | ✅ | Claude API 流式 + 按句切分 + 工具调用 |
| M1.4 TTS 集成 | ✅ | ElevenLabs eleven_flash_v2_5 WebSocket 流式 |
| M1.5 PC 客户端 | ✅ | 按键录音 → 上传 → 播放 TTS |
| M1.6 部署 VPS | 🟡 | 配置就绪（Docker/systemd/nginx），待实机部署 |
| M2 唤醒词接入 | ⬜ | Porcupine 替代按键触发 |
| M3 双端同步 | ⬜ | 多设备连接管理 + 话轮仲裁 |
| M4 打磨 | ✅ | 断线重连 + 能量过滤 + 工具集 + 取消支持 |

## 技术架构

```
[Android / PC 客户端]        [Android / PC 客户端]
    wss://                        wss://
        \                          /
         ┌────────────────────────────────┐
         │    brain 服务 (Python) v0.4.0    │
         │    FastAPI + WebSocket           │
         │    ┌──────────────────────────┐ │
         │    │ STT: sherpa-onnx         │ │  本地离线
         │    │ LLM: Claude API          │ │  流式 + Tool Calling
         │    │ TTS: ElevenLabs          │ │  WebSocket 流式
         │    │ Tools: 天气/计算/日期/搜索  │ │  只读安全
         │    └──────────────────────────┘ │
         │    ┌──────────────────────────┐ │
         │    │ 会话历史 + 取消支持        │ │  per-device
         │    │ 断线重连 + 保活 ping      │ │  M4
         │    └──────────────────────────┘ │
         └────────────────────────────────┘
```

## 核心特性

- **流式管线** — STT → LLM → TTS 全链路流式，首句 TTS 延迟 < 2s
- **Tool Calling** — LLM 可调用工具（天气、计算、日期、搜索），结果自动回传
- **对话历史** — per-device 最近 20 条消息，支持多轮上下文
- **取消支持** — 任意阶段可取消当前轮次
- **断线重连** — 指数退避（1s → 30s），重连后自动补发上下文
- **能量过滤** — 静音音频不上行，节省带宽和 API 调用
- **TTS 容错** — 单句合成失败不影响后续句子
- **管线指标** — 每轮日志记录 LLM/TTS 延迟、chunk 数、错误数

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 下载 STT 模型（~80MB + ~2MB，从 hf-mirror.com）
python download_models.py

# 3. 配置 API keys
cp .env.example .env
# 编辑 .env：
#   JARVIS_TOKEN=your-random-token
#   ANTHROPIC_API_KEY=your-claude-api-key
#   ELEVENLABS_API_KEY=your-elevenlabs-key

# 4. 启动服务
python server.py

# 5. 测试（另开终端）
python test_client.py         # WS 连通性测试
python test_stt.py            # STT 管线测试
python test_llm.py            # LLM + Tool Calling 测试

# 6. PC 客户端交互
python pc_client.py                        # localhost
python pc_client.py wss://your-vps/ws      # 远程 VPS
```

## PC 客户端操作

| 状态 | 按 Enter | 说明 |
|------|----------|------|
| 空闲 | 开始录音 | 发送 wake_event + 音频流 |
| 录音中 | 停止录音 | 发送 audio_done → 等待回复 |
| 等待回复 | 取消 | 发送 cancel → 回到空闲 |
| 播放中 | 取消 | 中断 TTS 播放 |

断线自动重连（指数退避 1s → 30s）。

## Web 客户端

浏览器直接访问 `http://your-server:8000/`，无需安装任何软件。

- Web Audio API 录音 → WebSocket 传输 → AudioContext 播放
- 支持 Chrome / Firefox / Edge（桌面）
- 实时状态显示：空闲 / 录音中 / 思考中 / 播放中

## 工具集

| 工具 | 功能 | API |
|------|------|-----|
| `get_weather` | 天气查询 | wttr.in（免费） |
| `calculate` | 数学计算 | 安全 eval（受限） |
| `get_datetime` | 日期时间 | 系统时钟 |
| `search_web` | 网络搜索 | DuckDuckGo Instant Answer（免费） |

所有工具只读。写操作工具（提醒等）需语音二次确认，待后续版本。

## 部署

```bash
# 方式一：Docker
docker-compose up -d

# 方式二：VPS 一键部署
bash deploy.sh

# 方式三：systemd 服务
cp jarvis-brain.service /etc/systemd/system/
systemctl enable --now jarvis-brain
```

详见 [nginx.conf](nginx.conf) 配置 HTTPS + WebSocket 代理。

## 协议

详见 [jarvis-assistant-spec-v0_3.md](jarvis-assistant-spec-v0_3.md)

## 技术栈

| 模块 | 选型 |
|------|------|
| STT | sherpa-onnx + Paraformer CN-small + Silero VAD（本地离线） |
| LLM | Claude API Haiku + streaming + Tool Calling |
| TTS | ElevenLabs eleven_flash_v2_5 WebSocket 流式 |
| 通信 | FastAPI + WebSocket (wss) + 30s keepalive |
| 客户端 | PC: Python + sounddevice / Android: Kotlin（待开发） |
| 部署 | Docker / systemd / nginx + Let's Encrypt |

## 项目结构

```
jarvis-brain/
├── server.py          # 主服务：WS 端点 + STT→LLM→TTS 管线
├── stt.py             # STT 引擎：sherpa-onnx + Silero VAD
├── llm.py             # LLM 引擎：Claude API 流式 + Tool Calling
├── tts.py             # TTS 引擎：ElevenLabs WebSocket 流式
├── tools.py           # 工具定义与执行（天气/计算/日期/搜索）
├── config.py          # 启动配置校验
├── pc_client.py       # PC 客户端（录音 + 播放 + 重连）
├── static/
│   └── index.html     # Web 客户端（浏览器语音交互）
├── download_models.py # STT 模型下载脚本
├── test_client.py     # WS 连通性测试
├── test_stt.py        # STT 管线测试
├── test_llm.py        # LLM 管线测试
├── deploy.sh          # VPS 部署脚本
├── Dockerfile         # Docker 镜像
├── docker-compose.yml # Docker Compose
├── jarvis-brain.service  # systemd 服务
├── nginx.conf         # nginx 反向代理
├── requirements.txt   # Python 依赖
├── .env.example       # 环境变量模板
└── jarvis-assistant-spec-v0_3.md  # 技术规格文档
```
