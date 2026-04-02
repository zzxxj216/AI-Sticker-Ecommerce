# Claude Code Windows 安装指南

## 一键安装（推荐）

打开 **PowerShell**，复制以下命令运行：

```powershell
irm http://你的IP:8899/install.ps1 | iex
```

> 安装过程全自动，无需手动输入任何内容，API Key 已预置。

---

## 手动安装

如果一键命令无法使用，按以下步骤手动操作。

### 第一步：安装 Node.js

前往 [https://nodejs.org](https://nodejs.org) 下载 **LTS 版本**安装包，一路默认安装即可。

安装完成后，打开 PowerShell 验证：

```powershell
node -v
```

输出版本号（如 `v22.x.x`）即表示安装成功，需要 **v18 或以上版本**。

---

### 第二步：安装 Claude Code

```powershell
npm install -g @anthropic-ai/claude-code
```

如果下载速度慢，先切换国内镜像再安装：

```powershell
npm config set registry https://registry.npmmirror.com
npm install -g @anthropic-ai/claude-code
```

---

### 第三步：配置环境变量

在 PowerShell 中运行以下命令（一次性永久生效）：

```powershell
[Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", "https://code.z-daha.cc", "User")
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-c75b02d02404fd12529f88ee0c223b2b016762ade35429162ba9c1183e949c33", "User")
[Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", "sk-c75b02d02404fd12529f88ee0c223b2b016762ade35429162ba9c1183e949c33", "User")
```

**重启 PowerShell** 使环境变量生效。

---

### 第四步：启动 Claude Code

```powershell
claude --dangerously-skip-permissions
```

首次启动会要求登录，按提示操作即可。

---

## 常见问题

### 提示"无法加载脚本"

PowerShell 默认禁止运行脚本，先执行以下命令解除限制：

```powershell
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
```

### node 不是可识别的命令

Node.js 安装后需要**重启 PowerShell** 才能识别，关闭当前窗口重新打开即可。

### 网络连接超时

尝试切换到国内 npm 镜像：

```powershell
npm config set registry https://registry.npmmirror.com
```

### 验证安装是否成功

```powershell
claude --version
```

能输出版本号即安装成功。

---

## 卸载

```powershell
npm uninstall -g @anthropic-ai/claude-code
```
