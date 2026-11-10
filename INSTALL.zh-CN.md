# 小米 10 Pro Switch 2 配对与 HID MTU 512 专用模块

这是根据当前手机实际提取的蓝牙库制作的实验版 Magisk 模块。当前 v5（`1.4-experimental` / versionCode 5）组合 HID MTU 512、Classic SSP 配对持久化、HID 双通道建立顺序修正，以及从蓝牙启动时生效的手柄设备类别编码。组合补丁已通过主机测试和当前手机的加载、Switch 2 配对、持续操作及保存密钥重连验证。首次观察会话持续 172.705 秒无非预期断开，游戏内 amiibo 尚未单独确认。

## 适用范围

| 项目 | 唯一适配目标 |
|---|---|
| 手机 | 小米 10 Pro，设备代号 `cmi` |
| 系统 | `V816.0.9.0.TJACNXM` |
| Android | 13，SDK 33，arm64 |
| Magisk | 最低 30.7（30700）；仍须通过启动阶段和实际挂载检查 |
| 目标库 | `/system_ext/lib64/libbluetooth_qti.so` |

安装时和每次开机都会检查设备、系统指纹、原库 SHA-256、补丁库 SHA-256 及 SELinux 标签。开机检查还会检查 Magisk 版本和挂载冲突。检查不通过就不加载补丁，并将模块停用。

原库 SHA-256：

```text
2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039
```

v5 补丁库 SHA-256：

```text
d537a6ffcac736537a9a3defe2178dc1cd8d29bd588ff1a71e3fe648f4d93bbe
```

## 改动

v5 共修改 **15 条 ARM64 指令、38 个字节**，文件大小和 ELF 布局不变。原有 6 条 MTU 指令继续提供以下行为：

- 报告长度检查允许至 512 字节，超过 512 仍拒绝。
- 对应消息分配从 78 字节扩大至 526 字节，容纳 14 字节前缀和 512 字节数据。
- 控制及中断通道的本地 MTU 参数由 64 改为 512。
- 对端 MTU 的默认值和上限改为 512，仍尊重对端提出的更小值。
- 错误日志同步显示新上限。

新增 1 条 Bond 指令在原生 Classic SSP 分类处把临时配对改为持久配对，使配对状态与 BTM 设备记录一致；用户确认、拒绝和取消配对语义保持。新增 5 条 HID 顺序指令仅在安全处理已经忙碌时，让提前到达的入站 HID 中断通道走原生等待路径，不跳过认证、不同主机或 HID 状态检查。

v5 另加 3 条 CoD 指令，直接把 HCI `Write_Class_Of_Device` 参数编码为 `08 25 00`，让启动时的首次写入即为手柄类别 `0x002508`。模块启用期间，所有经典蓝牙 CoD 写入都使用这一类别，与 Companion 原本持续修正 CoD 的目标一致；它不局限于 Joy-Con Droid 正在运行时。停用模块并重启可恢复原库行为。

Bond 修正不依赖 Companion 的通用 Bond hook，因此 Companion 的扫描警告可能仍出现。应通过当前补丁哈希、实际内存指令、密钥保存和重连结果判断，而不是只看警告是否消失。v5 没有修复 Mode-Change hook，也不保证所有断连或 amiibo 场景已解决。

模块在启动预检通过后，将载荷独立复制到 `/dev/cmi_hid_mtu512/`（目录仅 root 可访问），检查哈希和标签，再通过只读、可执行 bind mount 加载。这个临时副本不会沿用 `/adb/modules` 挂载来源，因此修复了当前 root 隐藏环境下蓝牙进程看不到旧模块的问题。它不写入原系统分区，也不修改 root 授权、Shamiko 或 SELinux 策略。永久 `skip_mount` 标记和不含 `system/` 的结构，确保启动脚本没有成功完成检查时不会由 Magisk 自动加载库。

## 安装

1. 从 [Releases](https://github.com/fenghengzhi/mi10pro-amiibo-mtu512/releases) 下载对应 v5 记录中 Assets 下的 `cmi-hid-mtu512-V816.0.9.0-v5.zip`，并用同页 `SHA256SUMS.txt` 校验；也可按 `BUILD.md` 自行构建。不要安装 GitHub 自动生成的 Source code ZIP。
2. 若旧版本正在加载，先在 Magisk 中停用旧模块并重启，确认目标库已恢复为上面的原库哈希。
3. 打开 Magisk → 模块 → 从本地安装，选择新 ZIP。
4. 安装成功后再次重启手机。
5. 确认蓝牙正常开启，按下面的方法验证实际加载，再测试 Switch 2 配对、持续连接和重连；amiibo 另行验证。

使用 Magisk App 安装；此包不提供 Recovery 安装器。若提示原库不匹配，不要修改校验值强行安装。更新此模块前，需要先停用旧版本并重启，以恢复原始库视图。

### Magisk 升级后模块自动关闭

v1/v2 只接受 Magisk 30700，升级后会在 `status.txt` 写入 `DISABLED: Magisk version changed; this build requires 30700`，并创建 `disable` 文件。因此反复手动开启再重启仍会关闭，而且该次启动不会加载补丁。

这一问题已由 v3（`1.2-experimental` / versionCode 3）改为最低版本检查解决，v4/v5 沿用该行为，与精确固件／库哈希检查分开。旧模块已自动停用并重启到原库视图时，可安装当前版本；确认模块开启后重启，再运行下面的只读验证器。无需降级 Magisk，也不要自行删除固件或哈希保护。

## 验证是否已加载

连接 ADB 后运行：

```sh
adb shell su -c 'cat /data/adb/modules/cmi_qti_hid_mtu512/status.txt'
adb shell su -c 'sha256sum /system_ext/lib64/libbluetooth_qti.so'
```

状态应以 `MOUNTED:` 开头，库哈希应与上面的补丁库哈希一致。`DISABLED:` 后会记录拒绝加载原因。**这些检查只代表该 root shell 的文件视图，不能证明蓝牙进程已经加载补丁。** v1 曾在此检查通过的同时，蓝牙进程仍使用原库。

在电脑执行只读验证器，检查实际蓝牙进程的文件哈希与十五处内存指令：

```sh
python3 verify_runtime.py --output runtime-check.json
```

在手机打开 JoyCon Droid 的 Pro Controller 页面触发 HID 初始化，再执行：

```sh
python3 verify_runtime.py --require-hid-initialized --output runtime-hid-check.json
```

两通道的初始化标志应为 1、MTU 应为 512。尚未初始化时可能均为 0；初始化后的字段也可能保留，因此这些值不代表当前一定仍注册或已经与 Switch 建立连接。验证器不会自动重启蓝牙、启动 App 或修改手机；可用 `--adb` 指定 adb 路径、`--serial` 选择设备，报告不保存序列号或 MAC。

验证器将 Magisk 版本作为诊断信息记录，不再因升级而直接停止检查；设备／固件不匹配或蓝牙进程所见库哈希未知时，仍拒绝读取固定内存偏移。模块是否生效应以进程指令和 HID 初始化值为准。

## 恢复原库

在 Magisk 中停用或卸载本模块，然后重启。也可以通过已授权的 ADB 执行：

```sh
adb shell su -c 'touch /data/adb/modules/cmi_qti_hid_mtu512/disable'
adb reboot
```

重启会清除 bind mount 和 `/dev` 临时副本，恢复原库。不要在蓝牙运行时手动卸载挂载。若无法进入 Android，可在支持访问 `/data/adb/modules` 的恢复环境中停用或移除该模块目录后重启。

## 验证范围

v5 主机验证包含 74 项 MTU、186 项 Bond、19 项 HID 顺序、83 项 CoD ARM64 行为测试，以及静态、模块保护、构建和运行时验证器测试。v2/v3 曾在当前手机实测加载与 HID 初始化后的 MTU 值。v4 增加了 Bond 和 HID 修正，但实机仍未稳定连接；HCI 显示部分启动中先写入手机类别 `0x5a020c`，Companion 随后才安装 CoD hook。v5 针对这个竞态增加原生编码，其验证结论来自 v5 自身的测试记录。

v5 的加载、Switch 2 配对、HID 顺序、持续连接和保存密钥重连结果已记录在[验证报告](VALIDATION.zh-CN.md)。抓包也观察到长 NFC/IR 报告，但游戏内 amiibo 成功仍未单独确认。模块不自动完成 JoyCon Droid 的手柄身份和配对配置。

512 字节报告数据还会附加 HID 头部；不要把报告上限理解为任何包含头部的 512 字节链路包都必然可发送。目标 amiibo 报告较小，实际仍以游戏测试为准。
