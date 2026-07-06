# 贡献指南

欢迎为这个项目提 Issue 或提交 Pull Request。

## 提交 Issue 前

- 先确认你使用的是最新版本，或说明你正在使用的版本号
- 尽量写清楚输入来源：客户端实例目录、`mrpack` 还是 `zip`
- 尽量写清楚加载器类型：`Fabric`、`Quilt`、`Forge`、`NeoForge`
- 如果是异常或失败问题，请附上复现步骤、报错截图或关键日志片段

## 提交 Pull Request 前

- 先开 Issue 或在现有 Issue 下讨论，避免改动方向不一致
- 改动尽量聚焦一个问题，不要顺手混入大范围无关整理
- 保持现有模块边界，不要把 UI、业务逻辑和文件操作重新耦合在一起
- 新增注释时优先写中文，并说明业务原因，不写显而易见的语法注释

## 本地检查

提交前至少完成一项最小检查。对当前仓库，推荐：

```powershell
python -m compileall .\自动筛选模组分类器.pyw .\auto_mod_classifier
```

如果你的改动影响了桌面界面，请至少手动确认相关页面能正常打开，基础操作链路没有明显回归。

## 不建议提交的本地私有内容

下面这些内容默认按本地私有文件处理：

- `AGENTS.md`
- `tests/`
- `auto_mod_classifier_settings.json`
- `auto_mod_classifier/db.sqlite`
- 本地打包缓存和临时产物

除非明确需要，否则不要把这些内容一起提交。
