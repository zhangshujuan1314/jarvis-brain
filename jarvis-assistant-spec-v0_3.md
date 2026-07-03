# 贾维斯语音助手 - 技术规格文档 v0.3

> 相对 v0.2 的变更（对抗性审查驱动）：
> 1. **新增部署拓扑一节**——三家云服务（Deepgram/Anthropic/ElevenLabs）均在境外，大陆直连不可达，brain 必须落在境外 VPS，这是 v0.2 遗漏的最大现实约束
> 2. **WS 协议补鉴权**——公网 WS 无鉴权 = 任何人可烧三家 API 额度
> 3. **修正协议 bug**——TTS 音频不再广播（否则双端同时播音），只回发起设备；广播仅限 session_sync
> 4. **重定义延迟指标**——从"唤醒→首包"改为"语音结束→首包"（前者含用户说话时长，不可控）；新增延迟预算分解与达标关键手段（LLM 流式分句喂 TTS）
> 5. **新增客户端状态机 + 半双工规则**——TTS 播放期间暂停唤醒检测，防回声自触发
> 6. **新增音频规格一节**——Porcupine 强制 16kHz/16bit/mono，之前未写明
> 7. **砍掉客户端 VAD 判尾**——改用 Deepgram endpointing 判断说话结束，客户端更简单、延迟更低
> 8. **选型落到具体型号**——TTS 明确 eleven_flash_v2_5（已核实支持中文、~75ms）；LLM 明确 Haiku 级快模型；Nova-3 中文为 2026 年新上能力，已核实流式可用

## 1. 目标与范围

### 1.1 一句话目标
Android 手机 + PC，任一设备说"贾维斯"唤醒后进入语音交互，由同一个云端"大脑"服务处理理解与执行，会话状态跨设备共享。

### 1.2 v1 范围内
- 本地唤醒词检测（离线、常驻，中文唤醒词"贾维斯"）
- 唤醒后录音流式上传 → 云端 STT（含 endpointing）→ LLM 流式理解 → 云端 TTS 流式回复
- Android 与 PC 接入同一会话，切换设备不丢上下文
- 基础 Tool Calling：先打通 1 个只读操作（查天气），验证链路
- WS 连接鉴权（预共享 token，v1 单用户够用）

### 1.3 v1 明确不做
- iOS
- 复杂多轮任务编排、视觉/多模态输入、声纹识别
- 完全离线运行（v1 假设 brain 在线）
- 打断（说话打断 TTS 播放）——列为 v2；v1 采用严格半双工
- 多用户/账号体系（单用户，token 写死在配置）

## 2. 部署拓扑（新增，P0 决策）

**约束**：Deepgram、Anthropic、ElevenLabs 的 API 均需境外网络直连，大陆环境不可达或不稳定。这决定了 brain 的部署位置不是实现细节，而是架构决策。

**v1 方案：brain 部署在境外 VPS**（复用现有 VPS）。

```
[Android / PC 客户端]
        │  wss://（跨境链路 ← 新瓶颈，见 §7 延迟预算与 §10 风险表）
        ▼
[境外 VPS: brain 服务]
        │  同区域/低延迟直连
        ├── Deepgram STT
        ├── Anthropic LLM
        └── ElevenLabs TTS
```

- 优点：brain → 三家 API 的调用稳定、低延迟；客户端只需维持一条到 VPS 的 wss 连接
- 代价：客户端 → brain 的跨境 RTT（实测通常 150–400ms，波动大）计入端到端延迟；跨境 WS 长连接可能被干扰，重连逻辑（M4.1）优先级上调
- **被否决的备选**：brain 部署本地/国内 + 代理出海——三条 API 长连接全走代理，故障面更大，且 PC 关机后 Android 端失效
- **保底备选（若跨境链路实测不可用）**：整体换国内供应商——STT/TTS 换火山引擎或阿里/讯飞，LLM 换 DeepSeek（Tool Calling 兼容 OpenAI 格式）。此路径不作 v1 首选，但 §11 路线图 M1.6 中安排一次链路实测作为 go/no-go 判定点

传输一律 `wss://`（TLS），域名 + Let's Encrypt，禁止裸 `ws://` 暴露公网。

## 3. 系统架构

```
┌──────────────┐                                ┌──────────────┐
│ Android 客户端 │                                │   PC 客户端    │
│ 唤醒词检测      │                                │ 唤醒词检测      │
│ (Porcupine)   │                                │ (Porcupine)   │
│ 录音 / 放音     │                                │ 录音 / 放音     │
│ 状态机（§4）    │                                │ 状态机（§4）    │
└──────┬───────┘                                └──────┬───────┘
       │ wss（鉴权后）：控制帧 JSON text frame              │
       │              音频帧 binary frame                 │
       ▼                                                 ▼
          ┌────────────────────────────────────────┐
          │        中心大脑服务 (Python, 境外 VPS)      │
          │  WS 网关：鉴权 / 连接管理 / 消息路由           │
          │  会话仲裁：同一时刻仅一台设备持有"话轮"         │
          │  STT: Deepgram Nova-3 流式 (language=zh)   │
          │       └ endpointing 判断说话结束            │
          │  LLM: Claude API 流式 (Haiku 级快模型)      │
          │       └ 按句切分，边生成边送 TTS              │
          │  TTS: ElevenLabs eleven_flash_v2_5 流式    │
          │  会话状态：按用户维度，v1 内存，重启清空         │
          └────────────────────────────────────────┘
```

**消息路由规则（修正 v0.2 的广播 bug）**：
- `stt_result`、`tts_audio`、`error`、`state` → **仅发给本轮发起设备**
- `session_sync` → 广播给该用户所有已连接设备（纯文本，用于跨端续接上下文）

## 4. 客户端状态机（新增）

```
IDLE（唤醒词监听中）
  │ 检测到"贾维斯"
  ▼
RECORDING（录音流式上传中，唤醒词检测暂停）
  │ 收到服务端 utterance_end        │ 超时兜底：录音 ≥15s 强制结束
  ▼
WAITING（等待回复，唤醒词检测暂停）
  │ 收到首个 tts_audio              │ 超时兜底：20s 未收到首包 → 报错回 IDLE
  ▼
PLAYING（播放 TTS，唤醒词检测暂停 ← 半双工，防回声自触发）
  │ 播放完毕 / 收到 error
  ▼
IDLE
```

规则：
- **半双工**：RECORDING / WAITING / PLAYING 三态均暂停唤醒词检测。防止 TTS 声音（尤其回复中出现"贾维斯"三字）或环境音在播放期间触发新一轮
- 每态设超时兜底，任何异常路径都回 IDLE 并给用户可见提示，绝不静默挂起
- 客户端 UI 最小化映射四态：监听中 / 录音中 / 思考中 / 播放中

## 5. 音频规格（新增）

| 项 | 规格 | 依据 |
|---|---|---|
| 上行（麦克风 → brain） | PCM 16kHz / 16bit / 单声道 | Porcupine 强制要求 16kHz/16bit/mono，帧长 512 samples；同一路音频直接续传给 Deepgram（`encoding=linear16&sample_rate=16000`），无需重采样 |
| 上行分块 | 每 100ms 一个 binary frame（3200 字节） | 平衡延迟与帧开销 |
| 下行（brain → 客户端） | ElevenLabs 输出 `pcm_16000`，binary frame 透传 | 客户端直接喂播放器，免解码；如带宽紧张 v2 再考虑 mp3/opus |

音频一律走 **WS binary frame**，不再 base64 进 JSON（v0.2 方案体积 +33% 且多一次编解码）。binary frame 首字节为通道标识：`0x01`=上行麦克风音频，`0x02`=下行 TTS 音频，其后紧跟 4 字节小端 turn_id，再后为 PCM 数据。

## 6. WebSocket 协议

### 6.1 鉴权（新增，P0）
连接建立后客户端必须在 5s 内发送鉴权帧，否则服务端断开：

```json
→ { "type": "auth", "token": "<预共享token>", "device_id": "android-01", "platform": "android" }
← { "type": "auth_ok", "server_time": 1735808000 }
← { "type": "auth_fail", "reason": "invalid_token" }   // 随后断开
```

v1 单用户，token 为环境变量配置的随机长字符串；user_id 隐含（单用户），schema 预留但不实现多用户。

### 6.2 控制帧（JSON text frame）

客户端 → 服务端：
```json
{ "type": "wake_event", "turn_id": 42 }          // turn_id 客户端自增，标识话轮
{ "type": "audio_done", "turn_id": 42 }          // 客户端主动结束（15s 兜底触发时用）
{ "type": "cancel", "turn_id": 42 }              // 用户手动取消本轮
```
（音频本身走 §5 定义的 binary frame，不在此列）

服务端 → 发起设备：
```json
{ "type": "turn_accepted", "turn_id": 42 }
{ "type": "turn_rejected", "turn_id": 42, "reason": "busy_other_device" }   // 双端冲突仲裁
{ "type": "utterance_end", "turn_id": 42 }        // Deepgram endpointing 判定说完，客户端停止录音
{ "type": "stt_result", "turn_id": 42, "text": "今天北京天气怎么样" }
{ "type": "state", "turn_id": 42, "value": "thinking" }
{ "type": "tts_done", "turn_id": 42 }             // 本轮音频已发完
{ "type": "error", "turn_id": 42, "stage": "stt|llm|tts|internal", "message": "..." }
```

服务端 → 全部设备（广播）：
```json
{ "type": "session_sync", "turn_id": 42, "user_text": "...", "assistant_text": "..." }
```

### 6.3 关键语义
- **turn_id 贯穿全链路**：客户端收到与当前 turn_id 不符的 tts_audio binary frame 直接丢弃（解决晚到包、取消后残留包问题）
- **说话结束判定以服务端为准**：唤醒后音频直接流式推 Deepgram，`utterance_end` 由 Deepgram endpointing 产生。客户端不做 VAD 判尾（v0.2 的客户端 VAD 方案删除），只保留 15s 最长录音兜底。收益：客户端逻辑更简单，且 STT 边说边转写，`utterance_end` 到 `stt_result` 几乎零间隔
- **双端冲突**：先到先得。brain 维护"当前话轮持有设备"，冲突方收 `turn_rejected`，客户端提示"正在处理另一设备请求"后回 IDLE

## 7. 延迟预算（新增，替代 v0.2 的单行指标）

**指标定义修正**：v0.2 的"唤醒触发→TTS 首包 <3s"包含用户说话时长，不可控、不可测。正确口径：

> **T = 用户语音结束（utterance_end 时刻）→ 客户端收到首个 tts_audio 帧**，目标 T < 2s，v1 放宽 < 3.5s

预算分解（境外 VPS 部署，客户端在大陆）：

| 环节 | 预估 | 说明 |
|---|---|---|
| Deepgram endpointing 判定 + final 结果 | 300–600ms | endpointing 静音阈值本身 ~300ms |
| LLM 首句生成（流式） | 500–1200ms | 首 token + 凑满第一个可合成句 |
| TTS 首包（eleven_flash_v2_5） | 100–300ms | 模型侧 ~75ms + 网络 |
| brain → 客户端跨境回传 | 150–400ms | 跨境 RTT，波动大 |
| **合计** | **约 1.1–2.5s** | 达标前提见下 |

**达标的两个强制架构约束**（不满足则必然超时）：
1. **LLM 流式输出按句切分喂 TTS**——首句一凑齐（遇到 。！？或长度阈值）立即发起 TTS 合成并下发，绝不等全文生成完。ElevenLabs WS 流式接口支持增量文本输入
2. **system prompt 强制口语化短回复**——语音场景回复控制在 1–3 句。长回复既慢又烧 TTS 字符费

## 8. 技术选型（已确认，型号落死）

| 模块 | 选型 | 理由 / 核实结论 |
|---|---|---|
| 唤醒词检测 | **Porcupine**（Picovoice） | 官方支持中文自定义唤醒词，Console 训练秒级完成；本地、离线、低功耗。注意：**.ppn 按平台绑定**，Android 和 PC（Windows/Linux）需各导一份；SDK 需 AccessKey，免费层用量限制开工前在 Console 核实 |
| STT | **Deepgram Nova-3**，`language=zh` | 已核实：Nova-3 于 2026 年扩展支持简体中文（zh / zh-CN），流式+批量均可用。此前 Nova-3 中文不可用是事实，v0.2 写"明确支持"时该能力刚上线不久——中文流式的实测准确率在 M1.2 单独验证，不达标退 Nova-2（长期支持中文）。新账号 $200 免费额度，个人用量近乎零成本 |
| TTS | **ElevenLabs `eleven_flash_v2_5`** | 已核实：Flash v2.5 支持中文（32 语言之一），~75ms 合成延迟，官方推荐用于实时 agent 场景。**不要用默认的 multilingual v2**（质量高但延迟和单价都更高）。ElevenLabs 是全链路最贵一环（约 $0.05/千字符量级，免费层额度很小），高频使用成本先算清；国内备选：MiniMax / 火山引擎 / Azure TTS（中文成熟且便宜一个量级） |
| LLM | **Claude API，Haiku 级快模型 + streaming** | 语音助手回合短、要快，用最快档模型；Tool Calling 现成。具体型号开工时查当前最新 Haiku 版本 |
| 通信 | **WebSocket（wss）** | 双向低延迟、服务端主动推送；控制帧 text / 音频帧 binary 分离（§5、§6） |
| 后端 | **Python（FastAPI + websockets）** | 音频/STT/TTS 生态成熟，异步友好 |
| Android 客户端 | **Kotlin 原生** | 前台服务/后台存活需要精确控制，原生比 RN 可控 |
| PC 客户端 | **Python（Windows 优先）** | 与后端同栈，Porcupine 有 Python SDK；音频用 sounddevice |

## 9. 非功能需求

- **延迟**：见 §7，口径为"语音结束→TTS 首包"，目标 <2s，v1 放宽 <3.5s
- **可靠性**：brain 对每个外部调用设超时（STT 连接 5s / LLM 首 token 15s / TTS 首包 8s）+ 1 次重试；任一环节失败发 `error` 帧，客户端语音或 UI 提示，绝不静默挂起
- **离线降级**：客户端检测 WS 断开后 UI 明示"大脑离线"，指数退避自动重连
- **安全**：wss + 预共享 token（§6.1）；token 泄露即换；brain 日志不落盘原始音频
- **隐私**：音频上传 Deepgram（STT）与 brain（转发），文本发 Anthropic 与 ElevenLabs。全链路第三方云。若此为硬红线需改本地方案（Whisper/Piper/sherpa-onnx），当前 spec 假设接受云端；唤醒词检测本身始终本地，未唤醒时无任何音频上传

## 10. 风险评估（对抗性审查）

| 风险 | 触发条件 | 影响 | 缓解/验证 |
|---|---|---|---|
| **Android 后台存活** | 系统后台限制、国产 ROM（MagicOS 等）主动杀进程 | 唤醒词服务被杀，Android 端形同虚设 | **P0，M0 最先单独验证**：前台服务 + 忽略电池优化 + 厂商白名单设置，锁屏挂 4–6h 实测 |
| **跨境 WS 链路质量**（新增） | 客户端→境外 VPS 链路抖动、长连接被干扰 | 延迟超标、频繁断线 | M1.6 真机跨境实测作为 go/no-go；不达标触发 §2 保底路径（全换国内供应商） |
| **无鉴权被薅**（新增） | brain 公网暴露 | API 额度被烧、隐私泄露 | §6.1 鉴权为 M1.1 一部分，先于任何外部 API 接入 |
| 中文唤醒词质量 | "贾维斯"三音节可能偏短（官方建议 ≥6 音素） | 误唤醒率高或训练不通过 | Console 训练时看质量判定；备选词准备一个更长的（如"贾维斯同学"） |
| 回声自触发（新增） | TTS 播放期间唤醒词检测仍在跑 | 自己打断自己、死循环 | §4 半双工状态机，播放期间强制暂停检测 |
| 三供应商拼接 | 任一超时/限流 | 单轮失败 | 全环节超时+重试+error 帧（§9） |
| 双端同时唤醒 | 两设备几乎同时触发 | 话轮归属不明 | §6.3 服务端仲裁，先到先得 + `turn_rejected` |
| 误唤醒 | 唤醒词误报 | 隐私 + 无谓 API 成本 | Porcupine 置信度阈值调优（M0.3 顺带统计误报率）；M4 加本地能量/VAD 二次过滤 |
| TTS 成本超预期（新增） | 高频使用 | ElevenLabs 账单失控 | M1.4 记录单轮字符数估算月成本；超预算切国内 TTS |
| Tool Calling 越权 | LLM 被诱导调用危险工具 | 实际系统被破坏 | v1 仅只读工具；写操作工具需语音二次确认 |

## 11. 开发路线图（原子步骤，逐个喂给 Claude Code）

### M0：验证 P0 风险——Android 后台存活（不涉及 brain）
- [ ] M0.1　Picovoice Console 训练中文唤醒词"贾维斯"，看质量判定；不通过换备选词。导出 **Android** 平台 .ppn（PC 平台的 M2.2 前再导）
- [ ] M0.2　纯 Android demo：Porcupine SDK + 前台服务，检测到唤醒词仅震动+日志，无录音上传
- [ ] M0.3　后台/锁屏/切 App 挂 4–6h：记录①是否被杀（含 MagicOS 电池优化白名单设置前后对比）②误唤醒次数（开着电视/播客跑）。**不通过则停下重新评估 Android 端方案**

### M1：PC 单机打通全链路 + 部署（先不接唤醒词）
- [ ] M1.1　brain 骨架：FastAPI + WS endpoint + **§6.1 token 鉴权** + echo 验证连通（鉴权在任何外部 API 之前落地）
- [ ] M1.2　接 Deepgram Nova-3 流式：本地录音文件推流测试，验证①中文识别准确率（Nova-3 中文是新能力，重点实测）②endpointing 的 `utterance_end` 时机是否符合 §6.3 设计
- [x] M1.3　接 Claude API：Haiku 快模型 + streaming + 查天气只读工具 + 按句切分逻辑，跑通"文字进、工具调用、分句流出"
- [x] M1.4　接 ElevenLabs `eleven_flash_v2_5` 流式：与 M1.3 的分句输出串起来，记录字符消耗
- [x] M1.5　PC 客户端：按键触发录音 → binary frame 上传 → 收 utterance_end 停录 → 播 TTS，本机 localhost 全链路打通
- [~] M1.6　**部署**：brain 上境外 VPS（域名 + wss + systemd），PC 客户端改连公网地址，实测跨境端到端延迟（§7 口径），做 go/no-go 判定。部署配置已就绪（Dockerfile / docker-compose / systemd / nginx / deploy.sh），待实机部署

### M2：接入唤醒词（M0、M1.6 双通过后开始）
- [ ] M2.1　Android 客户端完整实现：M0 demo + §4 状态机 + 录音上传 + 播放，连公网 brain 跑通"说贾维斯→语音对话"闭环
- [ ] M2.2　导出 PC 平台 .ppn，PC 客户端接唤醒词，替换按键触发

### M3：双端接入与同步
- [ ] M3.1　brain 多设备连接管理：话轮仲裁（§6.3）+ 路由规则（§3）+ session_sync 广播
- [ ] M3.2　验证：手机问一半，切电脑唤醒能接上下文；双端同时唤醒时后到方收到明确提示

### M4：打磨
- [x] M4.1　断线指数退避重连 + 会话保留窗口（重连后 session_sync 补发最近一轮）
- [x] M4.2　误唤醒过滤：本地能量检测/轻量 VAD，空录音不上传
- [x] M4.3　扩展工具集：只读工具（天气/计算器/日期/搜索），写操作待后续版本加语音二次确认

## 12. 待决策清单（不阻塞 M0/M1 开工）

| 决策项 | 默认值 | 备注 |
|---|---|---|
| 第一个工具 | 查天气 | 占位验证，随时换 |
| PC 平台 | Windows | 影响 M2.2 的 .ppn 导出平台 |
| 唤醒词备选 | "贾维斯同学" | 仅当"贾维斯"训练质量不达标时启用 |
| TTS 保底 | MiniMax / 火山引擎 | M1.4 成本实测或 M1.6 链路不达标时切换 |
| 全国产化保底 | 火山 STT/TTS + DeepSeek | 仅当 M1.6 跨境链路 go/no-go 判否时整体切换 |

## 13. 开工前需人工核实的事实（写死前最后一查）

- Picovoice 免费层的 AccessKey 用量限制与自定义唤醒词训练配额
- ElevenLabs 当前免费/付费档中文流式的实际额度与单价
- Claude 当前最新 Haiku 版本型号字符串
- Deepgram `language=zh` 流式 endpointing 参数默认值（`endpointing` 毫秒数是否需要针对中文调整）
