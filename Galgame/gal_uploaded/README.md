# Galgame 一键采集器

## 功能
- 多站按游戏名搜索：VNDB / Bangumi / Shionlib / SearchGal / 鲲Gal / TouchGal
- 批量采集网页标题、正文、图片与视频链接
- 导出 JSON / CSV / HTML
- **界面自定义**：导入顶部横幅图、窗口图标、背景色（自动记住）

## 安装运行
```bash
pip install -r requirements.txt
python galgame_crawler.py
```
或双击 `一键启动.bat`

## 自定义界面
窗口右上角：
- **导入横幅图**：顶部显示你选的图片（建议宽图，PNG 最佳）
- **导入图标**：更换窗口左上角图标
- **背景色**：选择窗口背景颜色
- **恢复默认界面**：清除自定义

图片会复制到程序目录下的 `ui_assets/`，配置在 `ui_config.json`。

## 注意
请遵守版权与各站条款，勿用于侵权传播。
