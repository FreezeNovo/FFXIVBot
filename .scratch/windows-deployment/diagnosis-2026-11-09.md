# Windows 解压部署失败诊断（2026-11-09）

范围：用户授权 SSH 检查指定 Windows 机器的 `D:\FFXIVBot-0.3.0-windows` 部署错误。只读查询安装状态、解释器元数据和版本；未重装、修改部署脚本、调整系统权限、启动应用或迁移业务数据库。

## 已验证结论

- 目标系统为 Windows 11 IoT Enterprise LTSC，Windows PowerShell `5.1.26100.9444`，uv `0.12.4`。
- 用户工具目录中的实际 Python 为 `cpython-3.14.7-windows-x86_64-none`。直接运行该目录下 `python.exe --version` 返回 `Python 3.14.7`，退出码0。
- uv 建立 `cpython-3.14-windows-x86_64-none` Junction，指向上述实际版本目录。通过该联接运行同一解释器失败。
- `.venv/pyvenv.cfg` 的 `home` 指向该联接，`.venv/Scripts/python.exe --version` 报：

```text
error: uv trampoline failed to spawn Python child process
  Caused by: entity not found (os error 2)
```

- 在与启动脚本一致的 `UV_PYTHON_INSTALL_DIR` 下，`uv python find 3.14 --offline --no-python-downloads` 报：

```text
Failed to query Python interpreter
无法遍历该路径，因为它包含不受信任的装入点。 (os error 448)
```

因此，本机失败路径是 uv 的无补丁版本别名联接不可遍历，导致引用该联接的虚拟环境无法启动；实际解释器文件存在且可执行。未进一步确定系统哪项安全配置令该联接不受信任，也未修改该配置。

## 日志与影响

- 部署根目录、`logs`、`data`、`data/logs` 未找到 `.log`/`.txt` 运行记录。现有启动脚本只把安装早期错误输出到控制台，没有持久化安装日志。
- `.env`、`data/ff14_bot.sqlite3`、`data/logs/p3.log` 均不存在，说明尚未进入配置生成或业务启动。诊断末尾再次检查，三项仍不存在。
- 首次尝试运行离线测试时，同样在启动 Python 之前失败；因此没有本次 Windows 测试通过的结论。

## 无副作用复现

以目标当前用户在 PowerShell 中执行；版本路径对应本次现场，不作为未来永久默认版本：

```powershell
$managed = Join-Path $env:LOCALAPPDATA 'FFXIVBot\python'
& "$managed\cpython-3.14.7-windows-x86_64-none\python.exe" --version
# Python 3.14.7；退出码0
& "$managed\cpython-3.14-windows-x86_64-none\python.exe" --version
# 路径无法运行
& 'D:\FFXIVBot-0.3.0-windows\.venv\Scripts\python.exe' --version
# uv trampoline failed ... os error 2
```

## 待修复方向

部署脚本应从已安装的实际版本目录选择并验证解释器，创建虚拟环境时使用经过验证的真实路径，避免依赖该机器不可遍历的 uv 别名 Junction。保留原环境后重建，再验证 Windows 原生依赖、测试和运行锁。此项尚未实施，不以关闭 Windows 安全保护作为默认解决方案。

安装阶段缺少持久日志是相邻运维缺口，可另行补充经过脱敏的安装诊断输出；不能直接将完整环境或真实连接参数写入日志。
