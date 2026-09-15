# CSU Library

中南大学图书馆座位预约助手，已适配 2026 年新版 H5/v4 接口和统一身份认证。

## 安装

```powershell
python -m pip install -r requirements.txt
```

## 配置

```ini
[DATABASE]
CAMPUS = '新校区'
SEAT = ['XF5B030', 'XF5F020']
DAYS_AHEAD = 1
```

- `SEAT` 按优先级排列。
- `DAYS_AHEAD = 0` 表示今天，`1` 表示明天。
- 预约时段不需要手动配置；程序使用服务器当天返回的固定时段，目前为 `07:30–22:00`。

## 安全测试

先做不需账号的公开接口检查：

```powershell
python helper.py --action probe
```

再做真实登录和座位查询测试：

```powershell
python helper.py --action test
```

`test` 会登录、读取服务器时段、查找配置的座位，但不会调用 `/v4/space/confirm` 预约确认接口。账号和密码只在本机交互输入，密码不回显、不写日志。

## 正式预约

只有下面的动作会真正提交预约：

```powershell
python helper.py --action reserve
```

定时任务建议从环境变量读取凭据，不要把密码写入代码或命令行：

```powershell
$env:CSU_USER = '<校园卡账号>'
$env:CSU_PASSWORD = '<统一身份认证密码>'
python helper.py --action reserve
```

如果统一身份认证要求验证码，程序会停止，不会继续尝试或提交预约。

## 每天 06:00 自动预约

仓库的 `.github/workflows/reserve.yml` 使用 GitHub Actions 自动运行，电脑关机也不影响。工作流在北京时间 05:55 启动、安装依赖，然后等到 06:00 调用 `reserve`。

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中配置：

- `USER`：校园卡账号。
- `PWD`：统一身份认证密码。

将代码提交并推送到默认分支后，先在 **Actions → CSU Library Reserve → Run workflow** 手动运行一次。手动运行默认选择 `test`，只验证云端登录和查座，不会预约；只有手动改选 `reserve` 才会立即正式预约。每日定时触发始终使用 `reserve`。

GitHub Actions 的定时任务仍可能因平台排队而延迟；05:55 预启动可以降低整点排队的影响，但不是硬实时保证。

## Windows 定时运行

需要准时在 06:00 提交预约时，建议使用保持开机联网的 Windows 电脑运行任务计划程序。完整配置、测试和排错步骤见 [Windows 定时预约教程](docs/Windows定时预约教程.md)。
