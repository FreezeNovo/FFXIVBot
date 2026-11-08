# Windows 部署检查修复与验收（2026-11-09）

## 问题与修复

用户控制台报告148项测试有2项error：网关子进程输出拼接None导致TypeError，NA新鲜度测试在Windows调用不存在的time.tzset。

- `tests/test_gateway.py`：空输出规范化为空字符串，但继续断言子进程退出码为0；明确UTF-8解码，传入必要的Windows系统/临时目录环境，不继承Bot连接配置；事件循环在应用网络阻断前创建，与生命周期测试保持相同方式。
- `tests/test_p3_na_freshness.py`：使用可控Honolulu隐式转换及同一时间点的带时区输入，验证固定北京时间输出，不再修改进程TZ或依赖POSIX专属API。原业务断言保留，Windows不跳过该测试。
- `tests/test_windows_test_compatibility.py`：新增成功子进程空stderr、失败子进程错误可见、无tzset三项回归。修复前同一命令3项error；修复后3项通过。
- `scripts/start-windows.ps1`：探测已安装真实补丁目录中的可运行Python，显式请求完整补丁版本创建/同步环境；失效或不同版本旧环境改名保留。现场实测证明仅向uv传入真实绝对路径仍会创建指向minor别名的环境，显式`3.14.7`才使home指向真实目录并正常运行。因此未采用关闭系统保护或修改目录联接安全设置的方案。

## 验证

本地命令：

```bash
.venv-linux/bin/python -m unittest discover -s tests -p 'test_windows_test_compatibility.py' -v
UV_CACHE_DIR=/tmp/ffxivbot-uv-cache bash scripts/check-linux.sh
```

三项回归先失败后通过；完整151项中150通过、1项Windows锁互操作测试因平台跳过，锁文件及30包兼容检查通过。继续沿用此前已确认可运行的隔离限制外离线检查环境，未启动业务服务。

Windows当前用户在`%TEMP%/FFXIVBot-compat-check-20261109-r3/FFXIVBot-0.3.0-windows`执行：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1 -CheckOnly
```

结果：退出码0，151项测试全部通过（41.903秒），包括实际网关事件/发送替身、8个WebUI路由的临时生命周期、系统锁及.NET/msvcrt互操作；使用31个Windows锁定包，Python3.14.7、uv0.12.4、PowerShell5.1。日志末尾为`Dependency and offline checks passed. Bot NOT started.`。`pyvenv.cfg`的home为真实补丁目录而非minor目录联接。

同目录第二次执行同一`-CheckOnly`再次退出0，151项全部通过（37.594秒）；`.venv.previous-*`数量保持0，确认复用已有健康环境，没有因PowerShell参数引用或版本探针错误反复重建。

检查后临时包内`.env`、`data/ff14_bot.sqlite3`均不存在。日志中的预演、网关与确认输出来自临时fixture测试，不代表业务实例启动或真实QQ连接。未覆盖原`D:\FFXIVBot-0.3.0-windows`目录，未读取真实凭据、迁移正式数据库或发送群消息；NapCat/Koishi未改动。

## 限制

Windows使用此前已经安装的当前用户uv和托管Python，验证了从空`.venv`重建依赖与全部检查；不把它等同于全新系统首次下载、UAC交互、GUI启动、真实连接、开机自启或长期运行验收。原安装阶段日志不足的缺口仍留待后续改进。
