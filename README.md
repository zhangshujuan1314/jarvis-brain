# 贾维斯语音助手 — 云端大脑服务

中文语音助手，Android + PC 双端。本地唤醒词（"贾维斯"）→ 流式语音交互，同一云端大脑处理理解与执行，会话跨设备共享。

## 开发状态

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| M1.1 brain 骨架 | ✅ 完成 | FastAPI + WS + token 鉴权（§6.1） |
| M1.2 STT 集成 | ✅ 完成 | sherpa-onnx Paraformer CN-small + Silero VAD，本地离线 |
| M1.3 LLM + Tool Calling | ⬜ 待开发 | Claude API Haiku + 流式按句切分 + 查天气工具 |
| M1.4 TTS 集成 | ⬜ 待开发 | ElevenLabs eleven_flash_v2_5 流式 |
| M1.5 PC 客户端 | ⬜ 待开发 | 按键触发录音 → 上传 → 播放 TTS，本机打通 |
| M1.6 部署 VPS | ⬜ 待开发 | 域名 + wss + systemd，跨境延迟实测 go/no-go |
| M2 唤醒词接入 | ⬜ 待开发 | Porcupine 唤醒词替代按键触发 |
| M3 双端同步 | ⬜ 待开发 | 多设备连接管理 + 话轮仲裁 + session_sync |
| M4 打磨 | ⬜ 待开发 | 断线重连 + 误唤醒过滤 + 扩展工具集 |

## 技术架构

```
[Android / PC 客户端]        [Android / PC 客户端]
    wss://                        wss://
        \                          /
         ┌────────────────────────┐
         │    brain 服务 (Python)   │
         │    FastAPI + WebSocket  │
         │    ┌──────────────────┐ │
         │    │ STT: Paraformer  │ │  本地离线
         │    │ LLM: Claude API  │ │  待接入
         │    │ TTS: ElevenLabs  │ │  待接入
         │    └──────────────────┘ │
         └────────────────────────┘
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 下载 STT 模型（~80MB + ~2MB，从 hf-mirror.com）
python download_models.py

# 3. 配置 token
cp .env.example .env
# 编辑 .env 文件设置 JARVIS_TOKEN

# 4. 启动服务
python server.py

# 5. 验证（另开终端）
python test_client.py         # M1.1: echo 连通性测试
python test_stt.py            # M1.2: WS 协议 + STT 管线测试
```

## 协议

详见 [jarvis-assistant-spec-v0_3.md](jarvis-assistant-spec-v0_3.md)

## 技术栈

| 模块 | 选型 |
|------|------|
| STT | sherpa-onnx + Paraformer CN-small + Silero VAD（本地离线） |
| LLM | Claude API Haiku 级快模型（待接入） |
| TTS | ElevenLabs eleven_flash_v2_5（待接入） |
| 通信 | FastAPI + WebSocket (wss) |
| 唤醒词 | Porcupine（客户端） |
| 客户端 | Android: Kotlin / PC: Python |
