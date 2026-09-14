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
SEAT = ['XF5A230', 'XF5A228']
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
