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

模块通过启动时的只读 bind mount 加载修改后的副本，不写入原系统分区。永久 `skip_mount` 标记和不含 `system/` 的结构，确保启动脚本没有成功完成检查时不会由 Magisk 自动加载库。

## 安装

1. 按 `BUILD.md` 构建，将生成的 `dist/cmi-hid-mtu512-V816.0.9.0-v1.zip` 复制到手机。
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

状态应以 `MOUNTED:` 开头，库哈希应与上面的补丁库哈希一致。`DISABLED:` 后会记录拒绝加载原因。挂载成功只说明补丁文件已生效；仍需确认蓝牙进程加载了它、JoyCon Droid 能配对并完成实际游戏中的 amiibo 测试。

## 恢复原库

在 Magisk 中停用或卸载本模块，然后重启。也可以通过已授权的 ADB 执行：

```sh
adb shell su -c 'touch /data/adb/modules/cmi_qti_hid_mtu512/disable'
adb reboot
```

重启会清除 bind mount，恢复原库。若无法进入 Android，可在支持访问 `/data/adb/modules` 的恢复环境中停用或移除该模块目录后重启。

## 验证范围

本次已对实际固件进行反汇编、源码对照及独立静态审查，并在手机上执行只读的兼容性预检。详细的 ARM64 模拟执行和模块保护测试结果见随附验证报告。

交付时没有安装模块、重启手机或在 Switch 上测试。这个模块属于经过本地验证的实验版，尚不能宣称真机蓝牙运行和 amiibo 端到端验证通过。它也不自动完成 JoyCon Droid 的 HID 注册、手柄身份和配对配置。

512 字节报告数据还会附加 HID 头部；不要把报告上限理解为任何包含头部的 512 字节链路包都必然可发送。目标 amiibo 报告较小，实际仍以游戏测试为准。
