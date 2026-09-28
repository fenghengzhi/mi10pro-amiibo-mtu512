# 小米 10 Pro 蓝牙 HID MTU 512 专用模块

这是根据当前手机实际提取的蓝牙库制作的实验版 Magisk 模块。它扩大经典蓝牙 HID Device 的报告上限及相关 MTU 参数，用于排除 JoyCon Droid amiibo 功能的 64 字节限制。

## 适用范围

| 项目 | 唯一适配目标 |
|---|---|
| 手机 | 小米 10 Pro，设备代号 `cmi` |
| 系统 | `V816.0.9.0.TJACNXM` |
| Android | 13，SDK 33，arm64 |
| Magisk | 30.7，版本号 30700 |
| 目标库 | `/system_ext/lib64/libbluetooth_qti.so` |

安装时和每次开机都会检查设备、系统指纹、原库 SHA-256、补丁库 SHA-256 及 SELinux 标签。开机检查还会检查 Magisk 版本和挂载冲突。检查不通过就不加载补丁，并将模块停用。

原库 SHA-256：

```text
2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039
```

补丁库 SHA-256：

```text
a3e40e1121eaecd02357315bf22f9f61a2be87944094b8161bd722fe8e081fa2
```

## 改动

原库确实把 HID Device 限制为 64。本模块只修改 6 条 ARM64 指令、共 6 个字节：

- 报告长度检查允许至 512 字节，超过 512 仍拒绝。
- 对应消息分配从 78 字节扩大至 526 字节，容纳 14 字节前缀和 512 字节数据。
- 控制及中断通道的本地 MTU 参数由 64 改为 512。
- 对端 MTU 的默认值和上限改为 512，仍尊重对端提出的更小值。
- 错误日志同步显示新上限。

模块在启动预检通过后，将载荷独立复制到 `/dev/cmi_hid_mtu512/`（目录仅 root 可访问），检查哈希和标签，再通过只读、可执行 bind mount 加载。这个临时副本不会沿用 `/adb/modules` 挂载来源，因此修复了当前 root 隐藏环境下蓝牙进程看不到旧模块的问题。它不写入原系统分区，也不修改 root 授权、Shamiko 或 SELinux 策略。永久 `skip_mount` 标记和不含 `system/` 的结构，确保启动脚本没有成功完成检查时不会由 Magisk 自动加载库。

## 安装

1. 按 `BUILD.md` 构建，将生成的 `dist/cmi-hid-mtu512-V816.0.9.0-v2.zip` 复制到手机。
2. 打开 Magisk → 模块 → 从本地安装，选择该 ZIP。
3. 安装成功后重启手机。
4. 确认蓝牙正常开启，再按 JoyCon Droid 的要求配置配对和测试 amiibo。

使用 Magisk App 安装；此包不提供 Recovery 安装器。若提示原库不匹配，不要修改校验值强行安装。更新此模块前，需要先停用旧版本并重启，以恢复原始库视图。

## 验证是否已加载

连接 ADB 后运行：

```sh
adb shell su -c 'cat /data/adb/modules/cmi_qti_hid_mtu512/status.txt'
adb shell su -c 'sha256sum /system_ext/lib64/libbluetooth_qti.so'
```

状态应以 `MOUNTED:` 开头，库哈希应与上面的补丁库哈希一致。`DISABLED:` 后会记录拒绝加载原因。**这些检查只代表该 root shell 的文件视图，不能证明蓝牙进程已经加载补丁。** v1 曾在此检查通过的同时，蓝牙进程仍使用原库。

在电脑执行只读验证器，检查实际蓝牙进程的文件哈希与六处内存指令：

```sh
python3 verify_runtime.py --output runtime-check.json
```

在手机打开 JoyCon Droid 的 Pro Controller 页面触发 HID 初始化，再执行：

```sh
python3 verify_runtime.py --require-hid-initialized --output runtime-hid-check.json
```

两通道的初始化标志应为 1、MTU 应为 512。尚未初始化时可能均为 0；初始化后的字段也可能保留，因此这些值不代表当前一定仍注册或已经与 Switch 建立连接。验证器不会自动重启蓝牙、启动 App 或修改手机；可用 `--adb` 指定 adb 路径、`--serial` 选择设备，报告不保存序列号或 MAC。

## 恢复原库

在 Magisk 中停用或卸载本模块，然后重启。也可以通过已授权的 ADB 执行：

```sh
adb shell su -c 'touch /data/adb/modules/cmi_qti_hid_mtu512/disable'
adb reboot
```

重启会清除 bind mount 和 `/dev` 临时副本，恢复原库。不要在蓝牙运行时手动卸载挂载。若无法进入 Android，可在支持访问 `/data/adb/modules` 的恢复环境中停用或移除该模块目录后重启。

## 验证范围

已完成实际固件分析、ARM64 模拟执行和模块保护测试，并在当前手机实测补丁加载与 HID 初始化后的 MTU 值。详细过程及完整重启结果见[验证报告](VALIDATION.zh-CN.md)。仍未验证 Switch 配对、无线长报告或游戏内 amiibo。模块也不自动完成 JoyCon Droid 的手柄身份和配对配置。

512 字节报告数据还会附加 HID 头部；不要把报告上限理解为任何包含头部的 512 字节链路包都必然可发送。目标 amiibo 报告较小，实际仍以游戏测试为准。
