# 金融投研智能体：团队开发入口

本目录目前是**项目设计与接口约定**，尚未实现 Web 服务或七项任务。已有初赛计划书及视频脚本。所有成员和 AI 编程助手从下列文件开始：

1. [项目总纲](docs/项目总纲.md)：目标、架构、分工、里程碑和完成标准。
2. [接口约定](docs/接口约定.md)：命名、字段、财务口径、七题输入输出和错误处理。
3. [OpenAPI 定义](contracts/openapi.yaml)：前后端 HTTP 接口。
4. [JSON Schema](contracts/contracts.schema.json)与[Python Protocol](contracts/ports.py)：服务、任务数据结构和模块边界。
5. [示例数据](contracts/examples/)：可用于前后端联调的固定样例。
6. [AI 协作说明](docs/给成员和AI助手的任务说明.md)：分工提示词、交付检查表、改接口流程。

**固定版本：** `api_version=1.0.0`，HTTP 路径 `/api/v1`，任务代码见接口约定。任何成员要更改公共字段，先修改 Schema、OpenAPI 和样例，再修改实现，并通知其他两人。

**检查契约：** 在项目根目录运行 `python3 -m pip install -r contracts/requirements.txt`，然后运行 `python3 contracts/validate_contracts.py`。样例公司代码 `999999` 及样例结果均为虚构的接口联调数据，不能作为竞赛实测成果。

现阶段的材料：[初赛计划书 PDF](项目计划书_初赛稿.pdf)、[实施路线](实施路线与验收清单.md)、[视频脚本](参赛视频脚本与制作方案.md)。
