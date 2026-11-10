# 小米 10 Pro · Switch 2 配对与 HID MTU 512

针对小米 10 Pro（`cmi`）精确原厂固件的 Magisk 实验模块。v5 组合 HID MTU 512、Classic SSP 配对持久化、HID 双通道建立顺序修正，以及从蓝牙启动时就生效的手柄设备类别（CoD）编码，用于 JoyCon Droid 的 Switch 2 连接验证。

**当前版本为 v5（`1.4-experimental` / versionCode 5）。** 保留 v2 的独立 tmpfs 加载方式及 v3 的最低 Magisk 30.7 检查，继续严格校验固件、库哈希、启动阶段与实际挂载。当前组合补丁已通过主机测试及本机验证：15 处指令实际加载，配对密钥持久保存，HID 等待路径实际触发，首次会话连续 172.705 秒无非预期断开；蓝牙重启后能使用保存的密钥重新连接。用户确认可正常操作。游戏内 amiibo 仍未单独确认，具体范围见[验证报告](VALIDATION.zh-CN.md)。

## 下载安装包

[最新构建](https://github.com/fenghengzhi/mi10pro-amiibo-mtu512/releases/latest) · [全部构建记录（从新到旧）](https://github.com/fenghengzhi/mi10pro-amiibo-mtu512/releases) · [GitHub Actions](https://github.com/fenghengzhi/mi10pro-amiibo-mtu512/actions/workflows/build-release.yml)

从对应 Release 的 Assets 下载 `cmi-hid-mtu512-V816.0.9.0-v5.zip`，并核对 `SHA256SUMS.txt` 和 `build-info.json` 中的版本与哈希。GitHub 自动生成的 **Source code ZIP 不能作为 Magisk 模块安装**。

每次推送到 `main` 或手动运行 Actions，都经过测试后创建独立 Release。发布记录带 UTC 时间、运行 ID 和重试次数，按从新到旧展示，旧包不覆盖、不自动删除。CI 测试通过不等于该次构建已经完成新的手机或 Switch 实测。

## 当前支持

| 项目 | 目标 |
|---|---|
| 设备 | 小米 10 Pro，`cmi` |
| 固件 | `V816.0.9.0.TJACNXM` |
| Android | 13 / SDK 33 / arm64 |
| Magisk | 最低 30.7 / 30700；已有 30.7、31.1 的实机记录，仍需通过每次启动的环境检查 |
| 修改目标 | `/system_ext/lib64/libbluetooth_qti.so` |
| 原库 SHA-256 | `2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039` |
| v5 补丁库 SHA-256 | `d537a6ffcac736537a9a3defe2178dc1cd8d29bd588ff1a71e3fe648f4d93bbe` |

共修改 **15 条 ARM64 指令、38 个字节**，文件大小和 ELF 布局不变：

- 原有 6 条 MTU 指令：报告长度检查、分配大小、两通道 MTU、对端 MTU 上限及日志。
- 1 条 Bond 指令：在原生 Classic SSP 分类处将临时配对设为持久配对，让配对状态与设备记录一致，保留用户确认和取消配对语义。
- 5 条 HID 顺序指令：仅在安全处理已经忙碌时，将提前到达的入站 HID 中断通道交给原生等待路径，保留认证、不同主机和 HID 状态检查。
- 3 条 CoD 指令：直接编码 HCI `Write_Class_Of_Device` 的三个参数字节为 `08 25 00`，使启动后的首次写入就是手柄类别 `0x002508`，不再依赖 Companion 异步安装 hook 的时机。

模块启用期间，所有经典蓝牙 CoD 写入都会使用该手柄类别，作用范围与 Companion 原本持续修正 CoD 的目标一致。停用模块并重启可恢复原库的编码行为。

Bond 修正直接作用于该固件的原生分类路径，不依赖 Companion 的通用 Bond hook 扫描；因此 Companion 仍可能输出找不到该 hook 的警告。应结合实际加载的指令和密钥持久化结果判断。v5 没有修复 Mode-Change hook，也未据此宣称所有断连或 amiibo 问题已解决。

## 内容

- [构建说明](BUILD.md)：使用仓库内的原库重建、验证和打包。
- [安装与恢复](INSTALL.zh-CN.md)：Magisk 安装、状态检查、停用与恢复。
- [验证报告](VALIDATION.zh-CN.md)：验证范围和已知限制。
- [原厂库快照](firmware/README.md)：按机型、固件版本保存的原始二进制和哈希。
- `patch_mtu.py`、`patch-instructions.s`：精确版本的补丁及独立汇编指令。
- `verify_patch.py`、`test_mtu_binary.py`、`test_bond_binary.py`、`test_hid_order_binary.py`、`test_cod_binary.py`：静态和 ARM64 行为测试。
- `mtu-module/`：模块脚本、构建器和保护逻辑测试。
- `binary-review.md`、`switch2-binary-review.md`、`mtu-source-audit.md`、`module-packaging-notes.md`：分析和审查依据。

本地验证通过：74 项 MTU、186 项 Bond、19 项 HID 顺序、83 项 CoD ARM64 行为测试，以及 44 项模块保护测试、11 项构建校验、12 项运行时验证器测试。原库确实在 64 字节处限制，组合补丁在模拟执行中仍允许至 512 并拒绝 513 及以上，同时正确扩充分配空间。这些测试模拟外部调用，不替代手机与 Switch 2 的端到端验证。

v1 直接绑定 `/data/adb/modules/` 中的文件，系统主进程和 Zygote 可见，但 2026-09-28 实测的 Shamiko 白名单环境下蓝牙进程仍看到原库。v2 在全部检查通过后，将补丁独立复制到 root 私有的 `/dev/cmi_hid_mtu512/`，再只读绑定到目标。无需给蓝牙进程 root 授权，也不改变 Shamiko、DenyList 或 SELinux 策略。仅检查 root shell 中的哈希不足以证明加载成功；`verify_runtime.py` 会检查实际蓝牙进程。

v2 还锁定了 Magisk 30700，升级到 31.1 后会在开机时写入 `disable` 并停止加载。v3 移除此精确版本限制；缺失、非法或过旧的版本号仍拒绝，后续环境检查不通过仍自动停用。运行时验证器也不再因 Magisk 版本不同而跳过诊断，但读取固定内存偏移前必须确认精确固件和已知原库／补丁库身份。

v4 增加 Bond 与 HID 顺序修正，共 12 条指令、26 个字节。实机复测仍未建立稳定连接：HCI 记录发现部分启动中先写入手机类别 `0x5a020c`，Companion 的 CoD hook 随后才安装，未追补已经发生的写入。v5 针对这一启动竞态增加原生 CoD 编码；v4 记录不能视为连接成功或 v5 的验证结论。

## 跟踪系统升级

当前原始快照位于 [`firmware/cmi/V816.0.9.0.TJACNXM/`](firmware/cmi/V816.0.9.0.TJACNXM/)。保存了活动蓝牙栈和配套 JNI 库，均为从手机提取的未修改文件；`manifest.json` 记录设备路径、大小、SHA-256、系统指纹和采集来源。

系统升级后新增版本目录，保留旧快照。对比两个版本的清单即可确定哪个原库发生变化，再分析新二进制；**不能仅更新模块的版本／哈希校验值便继续使用旧补丁**。步骤见[固件归档说明](firmware/README.md)。
