# Windows 部署指南

> 版本: v1.0.0

## 方式一：Task Scheduler（推荐开发环境）

```powershell
# 每日 09:00 L1 Meta-Loop
schtasks /create /tn "FTS-L1" /tr "python d:\Programs\factor_system\fts\cli.py meta-loop run" /sc daily /st 09:00

# 每日 23:00 L2 因子演化
schtasks /create /tn "FTS-L2" /tr "python d:\Programs\factor_system\fts\cli.py evolution run" /sc daily /st 23:00

# 每周一 06:00 L3 组合构建
schtasks /create /tn "FTS-L3" /tr "python d:\Programs\factor_system\fts\cli.py portfolio run" /sc weekly /d MON /st 06:00
```

## 方式二：NSSM Windows 服务（推荐生产环境）

1. 下载 NSSM: https://nssm.cc/download
2. 注册服务：
   ```powershell
   nssm install FTS-Scheduler "C:\Python312\python.exe" "d:\Programs\factor_system\fts\cli.py scheduler run"
   nssm set FTS-Scheduler AppDirectory "d:\Programs\factor_system"
   nssm set FTS-Scheduler AppStdout "d:\Programs\factor_system\logs\scheduler.log"
   nssm set FTS-Scheduler AppStderr "d:\Programs\factor_system\logs\scheduler.err"
   nssm set FTS-Scheduler Start SERVICE_AUTO_START
   nssm start FTS-Scheduler
   ```

## 方式三：后台进程

```powershell
# 守护模式
python d:\Programs\factor_system\fts\cli.py scheduler run --daemon

# 开发模式（热重载）
python d:\Programs\factor_system\fts\cli.py develop
```

## 日志管理

```powershell
# 查看日志
Get-Content d:\Programs\factor_system\logs\scheduler.log -Tail 50

# 日志位置
# d:\Programs\factor_system\logs\
```

## 自启动配置

将 `fts scheduler run --daemon` 添加到 Windows 启动项：
```powershell
$wshell = New-Object -ComObject WScript.Shell
$shortcut = $wshell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\FTS.lnk")
$shortcut.TargetPath = "C:\Python312\python.exe"
$shortcut.Arguments = "d:\Programs\factor_system\fts\cli.py scheduler run --daemon"
$shortcut.WorkingDirectory = "d:\Programs\factor_system"
$shortcut.Save()
```
