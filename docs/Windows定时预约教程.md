# Windows 定时预约教程

本文介绍如何在一台每天早上保持开机并能访问互联网的 Windows 电脑上，于北京时间 06:00 自动运行中南大学图书馆座位预约程序。

## 一、运行原理

每天 06:00，Windows 任务计划程序运行：

```text
D:\ProgramData\anaconda3\python.exe helper.py --action reserve
```

程序随后会：

1. 使用环境变量中的统一身份认证账号登录。
2. 查询 `config.ini` 中按优先级配置的座位。
3. 预约第一个可预约的目标座位。
4. 将结果写入 `library.log`。

`test` 只检查登录和座位，不产生预约；`reserve` 会提交真实预约。

## 二、当前配置

项目目录：

```text
D:\user_doc\life\pyq\csu\CSU-Library-main
```

Python：

```text
D:\ProgramData\anaconda3\python.exe
```

`config.ini` 当前配置为：

```ini
[DATABASE]
CAMPUS = '新校区'
SEAT = ['XF5B030','XF5F020']
DAYS_AHEAD = 1
```

- 程序优先预约 `XF5B030`，不可用时再尝试 `XF5F020`。
- `DAYS_AHEAD = 1` 表示预约明天。
- 预约时段由服务器返回，目前为 `07:30–22:00`，不需要手动配置。

## 三、安装依赖

打开 PowerShell，执行：

```powershell
Set-Location 'D:\user_doc\life\pyq\csu\CSU-Library-main'
& 'D:\ProgramData\anaconda3\python.exe' -m pip install -r requirements.txt
```

## 四、保存登录凭据

不要把校园卡密码写入代码、`config.ini` 或任务的命令行参数。

1. 按 `Win + R`。
2. 输入 `sysdm.cpl` 并回车。
3. 打开“高级 → 环境变量”。
4. 在“用户变量”中分别新建：

```text
变量名：CSU_USER
变量值：校园卡账号

变量名：CSU_PASSWORD
变量值：统一身份认证密码
```

设置后重新打开 PowerShell；必要时注销并重新登录 Windows。可以用下面的命令确认变量存在，该命令不会显示密码：

```powershell
[Environment]::GetEnvironmentVariable('CSU_USER', 'User')
-not [string]::IsNullOrWhiteSpace(
    [Environment]::GetEnvironmentVariable('CSU_PASSWORD', 'User')
)
```

第二条命令返回 `True` 代表密码变量存在。

> 用户环境变量并非加密保险箱。只建议在自己的 Windows 账户和个人电脑上使用，不要截图、分享或提交到 GitHub。

## 五、先进行安全测试

重新打开 PowerShell，执行：

```powershell
Set-Location 'D:\user_doc\life\pyq\csu\CSU-Library-main'
& 'D:\ProgramData\anaconda3\python.exe' helper.py --action test
```

正常日志类似：

```text
登录成功
DRY-RUN：未调用预约确认接口
座位 XF5B030：已找到
座位 XF5F020：已找到
测试通过：登录和座位查询可用，未产生预约
```

只有测试成功后才继续配置真实预约任务。

## 六、创建 Windows 计划任务

在开始菜单搜索“任务计划程序”，打开后点击右侧的“创建任务”，不要选择“创建基本任务”。

### 1. 常规

- 名称：`CSU Library Reserve`
- 选择“无论用户是否登录都要运行”。
- 勾选“使用最高权限运行”。
- 配置为当前使用的 Windows 版本。

保存任务时，Windows 可能要求输入当前 Windows 账户的密码。这里应输入 Windows 登录密码，而不是校园卡密码或 Windows Hello PIN。

如果电脑每天早上都处于已登录状态，也可以选择“只在用户登录时运行”。

### 2. 触发器

新建触发器并设置：

- 开始任务：按预定计划。
- 设置：每天。
- 开始时间：`06:00:00`。
- 勾选“已启用”。

### 3. 操作

新建“启动程序”操作。

“程序或脚本”：

```text
D:\ProgramData\anaconda3\python.exe
```

首次验证时，“添加参数”填写：

```text
helper.py --action test
```

“起始于”填写：

```text
D:\user_doc\life\pyq\csu\CSU-Library-main
```

“起始于”非常重要，否则程序可能找不到 `config.ini` 或无法在正确位置写入日志。

### 4. 条件

- 勾选“唤醒计算机运行此任务”。
- 笔记本电脑如需使用电池运行，可取消“只有在计算机使用交流电源时才启动”。
- 确保 Wi-Fi 或有线网络能够自动连接。

唤醒功能可以从睡眠状态恢复电脑，但不能启动一台已经完全关机的电脑。

### 5. 设置

- 勾选“允许按需运行”。
- 勾选“如果过了计划开始时间，立即启动任务”。
- “如果此任务已经运行”选择“不启动新实例”。
- 可以设置“任务运行超过 10 分钟则停止”。

## 七、验证计划任务

首次创建时保留 `--action test`：

1. 在任务计划程序中选中 `CSU Library Reserve`。
2. 右键选择“运行”。
3. 等待任务结束。
4. 查看任务的“上次运行结果”；`0x0` 通常代表成功。
5. 打开项目中的 `library.log`，确认出现 `DRY-RUN` 和“测试通过”。

验证完成后，编辑任务，将参数改为：

```text
helper.py --action reserve
```

保存后不要为了测试再次手动运行，否则程序会立即尝试真实预约。此后任务会在每天 06:00 自动执行。

## 八、运行要求

预约前请确认：

- 电脑没有关机；可以处于睡眠状态。
- 电源和网络稳定。
- Windows 时间和时区正确，建议启用自动校时。
- 任务状态为“准备就绪”，而不是“禁用”。
- `CSU_USER`、`CSU_PASSWORD` 仍然有效。
- 校园统一身份认证没有临时要求验证码。

Windows 更新、断电、彻底关机或网络未自动连接，都会导致任务无法准时执行。

## 九、查看预约结果

可以通过以下位置判断结果：

1. 任务计划程序中的“历史记录”和“上次运行结果”。
2. 项目目录中的 `library.log`。
3. 图书馆 H5 页面中的“我的预约”。

常见日志含义：

- `测试通过`：登录和座位查询正常，但没有预约。
- `预约成功`：已经提交并成功创建预约。
- `当前用户在该时段已存在座位预约`：账号已经有同时间段预约，服务器拒绝重复预约；这不是座位查询故障。
- `登录失败`：检查账号、密码、认证页面变化或验证码要求。
- `服务器异常`：可能是接口临时故障，检查稍后的手动测试结果。

## 十、GitHub Actions 备用任务

仓库仍配置了 GitHub Actions：北京时间 05:55 计划触发，06:00 提交真实预约。如果 GitHub 到 06:00 后才分配 Runner，工作流会跳过等待并立即尝试预约。

GitHub 的定时任务可能长时间排队，因此不应作为唯一的准点方案。可以先保留它作为备用：Windows 已经预约成功时，GitHub 后续运行只会得到“已有预约，不能重复预约”。

确认 Windows 任务连续正常执行后，如需关闭 GitHub 定时任务：

1. 打开 GitHub 仓库的 `Actions` 页面。
2. 选择 `CSU Library Reserve`。
3. 点击右上角 `…`。
4. 选择 `Disable workflow`。

手动运行 GitHub 工作流时，`test` 只检查；选择 `reserve` 会立即提交真实预约，不会等到第二天六点。

## 十一、修改目标座位

编辑 `config.ini` 中的 `SEAT`：

```ini
SEAT = ['第一优先座位','第二优先座位']
```

修改后先执行一次：

```powershell
& 'D:\ProgramData\anaconda3\python.exe' helper.py --action test
```

确认日志能找到新座位后再继续使用自动预约。
