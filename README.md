# 小米 10 Pro · HID MTU 512

针对小米 10 Pro（`cmi`）当前原厂系统的 Magisk 实验模块。通过修改实际固件的高通蓝牙库，把经典蓝牙 HID Device 的报告上限及相关 MTU 参数从 64 调整为 512，供 JoyCon Droid 的 amiibo 功能验证使用。

**补丁已完成静态审查和本地模拟测试；尚未安装到手机，尚未通过真实蓝牙／Switch／amiibo 验证。**

## 当前支持

| 项目 | 目标 |
|---|---|
| 设备 | 小米 10 Pro，`cmi` |
| 固件 | `V816.0.9.0.TJACNXM` |
| Android | 13 / SDK 33 / arm64 |
| Magisk | 30.7 / 30700 |
| 修改目标 | `/system_ext/lib64/libbluetooth_qti.so` |
| 原库 SHA-256 | `2c44ebea6313ca5d9b8c21a1e12816af15056fd15e2b104b5ba182e247bdc039` |

只修改 6 条 ARM64 指令、6 个字节。修改包含报告长度检查、对应分配大小、两个通道的 MTU 参数、对端 MTU 上限及日志；文件大小和 ELF 布局不变。模块安装和启动时会检查精确固件及库哈希，不匹配则拒绝加载。

## 内容

- [构建说明](BUILD.md)：使用仓库内的原库重建、验证和打包。
- [安装与恢复](INSTALL.zh-CN.md)：Magisk 安装、状态检查、停用与恢复。
- [验证报告](VALIDATION.zh-CN.md)：验证范围和已知限制。
- [原厂库快照](firmware/README.md)：按机型、固件版本保存的原始二进制和哈希。
- `patch_mtu.py`、`patch-instructions.s`：精确版本的补丁及独立汇编指令。
- `verify_patch.py`、`test_mtu_binary.py`：静态和 ARM64 行为测试。
- `mtu-module/`：模块脚本、构建器和保护逻辑测试。
- `binary-review.md`、`mtu-source-audit.md`、`module-packaging-notes.md`：分析和审查依据。

本地验证通过：74 项 ARM64 行为测试、19 项模块保护测试、11 项构建校验。原库确实在 64 字节处限制，补丁在模拟执行中允许至 512 并拒绝 513 及以上，同时正确扩充分配空间。

## 跟踪系统升级

当前原始快照位于 [`firmware/cmi/V816.0.9.0.TJACNXM/`](firmware/cmi/V816.0.9.0.TJACNXM/)。保存了活动蓝牙栈和配套 JNI 库，均为从手机提取的未修改文件；`manifest.json` 记录设备路径、大小、SHA-256、系统指纹和采集来源。

系统升级后新增版本目录，保留旧快照。对比两个版本的清单即可确定哪个原库发生变化，再分析新二进制；**不能仅更新模块的版本／哈希校验值便继续使用旧补丁**。步骤见[固件归档说明](firmware/README.md)。
