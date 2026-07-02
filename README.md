# Jarvis Brain

中文语音助手云端大脑服务。Android + PC 双端，本地唤醒词 → 流式 STT → LLM → TTS。

## 状态

**M1.2 进行中**：WS 协议 + token 鉴权 + 本地 STT（sherpa-onnx Paraformer 中文）

| 里程碑 | 状态 |
|--------|------|
| M1.1 brain 骨架 | ✅ 完成 |
| M1.2 STT 集成 | ✅ 完成（待真机语音验证） |
| M1.3 LLM + Tool Calling | 待开发 |
| M1.4 TTS 集成 | 待开发 |

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 下载 STT 模型（~80MB Paraformer + ~2MB VAD）
python download_models.py

# 3. 配置 token
cp .env.example .env
# 编辑 .env，设置 JARVIS_TOKEN

# 4. 启动
python server.py

# 5. 验证（另一个终端）
python test_client.py        # M1.1 echo 测试
python test_stt.py            # M1.2 STT 协议测试
```

## 技术栈

- **STT**: sherpa-onnx + Paraformer CN-small + Silero VAD（本地，离线）
- **LLM**: Claude API（待接入）
- **TTS**: ElevenLabs（待接入）
- **通信**: FastAPI + WebSocket（wss）
- **协议**: 见 spec 文档 §6
