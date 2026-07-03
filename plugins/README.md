# Jarvis Plugin System — 设备与应用控制

## 架构

```
LLM → tool_call → Plugin Router → 具体插件 → 设备/API
```

每个插件是一个 Python 模块，导出 `TOOLS`（工具定义）和 `execute()`（执行函数）。

## 内置插件

| 插件 | 功能 | 配置 |
|------|------|------|
| `smart_home` | 智能家居控制 | Home Assistant / Hue / MQTT |
| `media` | 媒体播放控制 | 系统音量 / Spotify |
| `app_control` | 应用启动 | PC 应用 / Android Intent |
| `webhook` | HTTP API 触发 | IFTTT / Bark / ServerChan |

## 快速配置

### 1. 智能家居（Home Assistant）

```bash
# .env
HOME_ASSISTANT_URL=http://homeassistant.local:8123
HOME_ASSISTANT_TOKEN=your-long-lived-token
```

编辑 `plugins/devices.json` 添加设备：
```json
{
  "客厅灯": {
    "platform": "home_assistant",
    "id": "light.living_room",
    "type": "light"
  }
}
```

### 2. Philips Hue

```bash
# .env
HUE_BRIDGE_IP=192.168.1.100
HUE_API_KEY=your-hue-api-key
```

### 3. 通知推送

```bash
# Bark (iOS)
BARK_URL=https://api.day.app/YOUR_KEY

# ServerChan (微信)
SERVERCHAN_KEY=your-key
```

### 4. Spotify

```bash
SPOTIFY_TOKEN=your-spotify-token
```

## 自定义插件

在 `plugins/custom/` 创建 `.py` 文件：

```python
TOOLS = [{
    "name": "my_tool",
    "description": "What it does",
    "input_schema": {
        "type": "object",
        "properties": {
            "param": {"type": "string"}
        },
        "required": ["param"]
    }
}]

async def execute(name: str, args: dict) -> str:
    if name == "my_tool":
        return json.dumps({"result": "done"})
    return json.dumps({"error": "unknown"})
```

自动加载，无需修改任何代码。

## 设备控制示例

| 用户说 | LLM 调用 | 实际操作 |
|--------|----------|----------|
| "把客厅灯调暗" | `smart_home_control(device="客厅灯", action="dim", value="50")` | Home Assistant API |
| "播放音乐" | `media_control(action="play")` | 系统媒体键 |
| "打开微信" | `open_app(name="微信")` | 启动应用 |
| "给手机发通知" | `send_notification(title="提醒", body="...")` | Bark/ServerChan |
| "执行晚安场景" | `smart_home_scene(scene="睡眠模式")` | Home Assistant Scene |
