# Porcupine Wake Word Assets

在此目录放置 Porcupine 唤醒词文件：

## 获取 .ppn 文件

1. 访问 https://console.picovoice.ai/
2. 注册/登录
3. 创建自定义唤醒词：输入 "贾维斯"（中文）
4. 选择平台：Android
5. 下载 `.ppn` 文件
6. 重命名为 `jarvis_zh_android.ppn` 放入此目录

## 文件命名

- `jarvis_zh_android.ppn` — 主唤醒词 "贾维斯"
- 如需备选词 "贾维斯同学"，命名为 `jarvis_long_zh_android.ppn`

## 注意

- `.ppn` 文件按平台绑定，Android 和 PC 需各导一份
- 免费层支持 3 个自定义唤醒词
- AccessKey 在 `local.properties` 中设置：`PORCUPINE_ACCESS_KEY=xxx`
