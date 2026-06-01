# Resources 模块

课程资料：解析 PDF/DOCX/EPUB 等、章节 outline、页码预览、证据检索。实现文件在上级 `resource_*.py`。

## 核心文件

| 文件 | 职责 |
|------|------|
| [`../resource_library.py`](../resource_library.py) | 解析入口、格式探测、章节抽取（最大模块） |
| [`../resource_service.py`](../resource_service.py) | 资料 metadata、删除、队列状态 |
| [`../resource_resolver.py`](../resource_resolver.py) | Chat 引用时的资料定位 |
| [`../resource_reindex.py`](../resource_reindex.py) | 重建索引 / OCR |
| [`../resource_parser_adapter.py`](../resource_parser_adapter.py) | 可选外部解析命令（`OPENCLASS_RESOURCE_PARSER_COMMAND`） |

## HTTP 入口

[`../../routers/resources.py`](../../routers/resources.py) — 删除、页预览等

## 注意

- **`POST /api/resources/upload` 已移除**；资料进入课程包的路径：协作 fork、Chat 导入、测试/脚本、DOCX
- 解析走确定性规则 + 可选 embedding；不在核心路径写教材目录
- 上传目录由 `OPENCLASS_UPLOAD_DIR` 配置，DB 只存 metadata 与路径
