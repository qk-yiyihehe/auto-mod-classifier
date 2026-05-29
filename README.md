# 自动筛选模组分类器

一个用于自动筛选 Minecraft 模组的桌面工具，支持按“服务端保留 / 纯客户端移出 / 无法分类”进行整理。

当前支持的加载器元数据：

- Fabric: `fabric.mod.json`
- Quilt: `quilt.mod.json`
- Forge: `META-INF/mods.toml`
- NeoForge: `META-INF/mods.toml` / `META-INF/neoforge.mods.toml`

分类依据：

1. 读取 jar 内部元数据
2. 查询 Modrinth 的 `client_side / server_side`
3. 必要时查询 MC百科“运行环境”作为兜底

## 功能

- 选择任意 `mods` 文件夹后自动扫描
- 自动识别主流加载器
- 将纯客户端模组移动到结果目录
- 将无法确定的模组单独归档
- 生成 `json / csv / txt` 分类报告

## 运行

如果本机已安装 Python 3：

双击 `启动自动筛选模组分类器.bat`

或执行：

```powershell
pythonw .\自动筛选模组分类器.pyw
```

## 打包 exe

```powershell
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name 自动筛选模组分类器 .\自动筛选模组分类器.pyw
```

## 注意

- 需要联网，才能查询 Modrinth / MC百科
- 程序会移动模组文件，建议先备份或先用试运行模式
- 某些库模组可能是“服务端可选”，不应仅凭 `client` 入口点直接判为纯客户端
