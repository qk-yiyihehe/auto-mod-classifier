# 自动筛选模组分类器

[![Release](https://img.shields.io/github/v/release/qk-yiyihehe/auto-mod-classifier?display_name=tag)](https://github.com/qk-yiyihehe/auto-mod-classifier/releases)
[![License](https://img.shields.io/github/license/qk-yiyihehe/auto-mod-classifier)](./LICENSE)

一个面向 Minecraft 整合包和服务端整理场景的 Windows 桌面工具。它把"模组分类"和"一键制作服务端"放到同一条工作流里，尽量减少手动筛选、反复导入和排错的时间。

当前主线版本是 `3.02`。历史对外版本可参考 GitHub Releases。

## 项目能做什么

- 自动分类模组，区分 `服务端保留`、`纯客户端` 和 `待人工确认`
- 支持 `Fabric`、`Quilt`、`Forge`、`NeoForge`
- 支持从客户端实例目录、`mrpack` 包和 `zip` 整合包直接制作服务端
- 自动识别 Minecraft 版本、加载器类型和精确加载器版本
- 自动准备安装器、依赖文件、启动脚本和常见配置目录
- 自动检查本机 Java，缺失时支持下载并补齐
- 输出 `CSV`、`JSON`、`TXT` 报告，并保留更直观的结果预览
- 在开服失败时整理关键报错片段，方便继续人工排查

## 核心能力

### 1. 模组筛选

程序会先读模组自身元数据，再按需要继续使用本地离线库、Modrinth、MC百科 和 CurseForge 做补充判断。

- 优先用本地信息做快速判断，减少不必要的联网请求
- 支持 `2次筛选`，只回头重试首轮 `unknown`
- MC百科 验证码页面支持手动处理后继续
- CurseForge 可以作为补充兜底来源，但更适合辅助判断，不建议单独依赖
- 会单独标记损坏 Jar 或异常 Jar

### 2. 一键制作服务端

程序可以把客户端实例目录或整合包输入，转换成可启动的服务端目录。

- 自动识别版本与加载器
- 自动下载并执行官方安装器
- 自动复制可用模组、常见配置目录和必要启动文件
- 自动首启并写入 `eula=true`
- 自动进行一次启动验证，尽量把问题暴露在制作阶段

### 3. 下载与诊断

- 下载链路会按场景选择更合适的源
- 缺少 Java 时支持自动补齐
- 开服失败时会补充更具体的原因提示
- 关键错误日志支持复制，方便继续发给 AI 或手动分析

## 适用场景

- 你想把整合包里的纯客户端模组尽快筛出去
- 你需要从现成客户端实例快速整理出服务端
- 你不想手动来回查模组页面、抄版本、补依赖
- 你希望在开服失败时先拿到一版可读的诊断信息

## 运行方式

### 直接运行源码

```powershell
pythonw .\自动筛选模组分类器.pyw
```

也可以双击：

```text
启动自动筛选模组分类器.bat
```

### 打包

```text
打包发布.bat
```

打包完成后，`dist` 目录里会生成：

- `自动筛选模组分类器.exe`
- `auto-mod-classifier-3.02.exe`

第二个文件名用于发布包分发。

## 下载

- GitHub Releases: https://github.com/qk-yiyihehe/auto-mod-classifier/releases
- 蓝奏云链接：https://wwaov.lanzouw.com/i6bJn3tyjd5e

## 运行要求

- 当前主要面向 Windows 桌面环境
- 如果需要 MC百科 或部分 CurseForge 兜底链路，机器上最好安装 `Chrome` 或 `Edge`
- 联网筛选速度会受网络环境影响
- `Quilt` 当前保留模组筛选识别，但不进入自动制作服务端流程
- 一键制作服务端不会修改客户端源目录

## 项目结构

```text
auto_mod_classifier/
  application/      应用编排与用例
  classifier/       模组分类、联网查询、离线库判定
  infrastructure/   导入与输入适配
  server_builder/   服务端制作流程
  ui/               Qt 界面
  bootstrap.py      启动装配
  tasks.py          任务编排
  shared.py         共享常量与公共定义
```

## 版本说明

- `main`：当前主线
- `v3.0.2`：3.02 对外版本
- `v2.0.0`：第一版对外发布版本
- `v2.0.1`：2.01 对外版本

## 贡献

欢迎提交 Issue 和 Pull Request。

- 提 Bug 时，尽量附上版本号、整合包类型、加载器类型、复现步骤和日志片段
- 提需求前，建议先说明你的实际使用场景
- 提交 PR 前，请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)

## 许可证

本项目采用 [MIT License](./LICENSE)。
