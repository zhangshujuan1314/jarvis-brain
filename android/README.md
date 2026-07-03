# Jarvis Brain — Android 客户端

## 架构

```
┌─────────────────────────────────┐
│         MainActivity            │
│  配置 UI (server URI + token)   │
└──────────┬──────────────────────┘
           │ start/stop
           ▼
┌─────────────────────────────────┐
│        JarvisService            │
│  前台服务 (foreground service)   │
│  ┌───────────┐ ┌──────────────┐ │
│  │ WakeWord  │ │ BrainClient  │ │
│  │ Porcupine │ │ WebSocket    │ │
│  └─────┬─────┘ └──────┬───────┘ │
│        │              │         │
│  ┌─────▼──────────────▼───────┐ │
│  │     JarvisStateMachine     │ │
│  │ IDLE→REC→WAIT→PLAY→IDLE   │ │
│  └─────────────┬──────────────┘ │
│  ┌─────────────▼──────────────┐ │
│  │       AudioManager         │ │
│  │  录音 (16kHz/16bit/mono)   │ │
│  │  播放 (PCM → speaker)      │ │
│  └────────────────────────────┘ │
└─────────────────────────────────┘
```

## 构建

### 1. 设置 Porcupine AccessKey

在 `local.properties` 或环境变量中设置：
```properties
PORCUPINE_ACCESS_KEY=your-access-key
```

免费获取：https://console.picovoice.ai/

### 2. 训练唤醒词

1. 登录 Picovoice Console
2. 创建唤醒词 "贾维斯"（中文）
3. 下载 Android 平台的 `.ppn` 文件
4. 放入 `app/src/main/assets/jarvis_zh_android.ppn`

### 3. 构建

```bash
cd android
./gradlew assembleDebug

# APK 输出: app/build/outputs/apk/debug/app-debug.apk
```

## 权限

| 权限 | 用途 |
|------|------|
| `RECORD_AUDIO` | 麦克风录音 |
| `INTERNET` | WebSocket 连接 |
| `FOREGROUND_SERVICE` | 后台运行 |
| `POST_NOTIFICATIONS` | 前台服务通知 |
| `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` | 防止系统杀进程 |
| `WAKE_LOCK` | 保持 CPU 唤醒 |
| `BOOT_COMPLETED` | 开机自启 |

## 状态机 (§4)

```
IDLE (唤醒词监听中)
  │ 检测到"贾维斯"
  ▼
RECORDING (录音流式上传中)
  │ utterance_end / 15s 超时
  ▼
WAITING (等待 LLM 回复)
  │ 收到首个 TTS 音频
  ▼
PLAYING (播放 TTS)
  │ 播放完毕
  ▼
IDLE
```

## 半双工规则

RECORDING/WAITING/PLAYING 期间，唤醒词检测暂停。
防止 TTS 声音触发回声自唤醒。

## 对抗性审查

- **后台存活**：前台服务 + 电池优化豁免 + START_STICKY
- **内存泄漏**：所有资源在 onDestroy 中释放
- **状态竞态**：StateMachine 使用 synchronized
- **音频格式**：严格 16kHz/16bit/mono（Porcupine 要求）
- **断线重连**：指数退避 1s → 30s（§M4.1）
- **唤醒词安全**：本地离线处理，未唤醒时无音频上传
