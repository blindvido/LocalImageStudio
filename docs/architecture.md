# 架构与维护说明

## 设计范围

绘境采用单进程 Python aiohttp 服务与原生浏览器界面。主服务负责本地 API、任务调度和图片读取；功能按文件拆分，持久层为本地 JSON 与文件目录。当前结构适合个人本地工作流，不提供多用户数据库事务或公网身份认证。

| 文件 / 目录 | 职责 |
| --- | --- |
| `studio_server.py` | 应用入口、路由注册、任务状态及 ComfyUI 集成 |
| `static/` | 页面、提示词模块、人物卡、历史、审批和编辑交互 |
| `storage.py` | 配置、工作流、历史、人物和分镜的 JSON 存储；临时文件替换写入 |
| `clients.py`、`network.py` | ComfyUI 与文字服务请求，系统代理处理 |
| `workflow.py` | ComfyUI 工作流检查、占位符替换与节点映射 |
| `novelai.py`、`nai_routes.py` | NovelAI 请求组装、参数校验及路由 |
| `novelai_limits.py` | 持久化提交间隔和使用计数 |
| `nai_redraw.py` | 已有图的 NAI 重绘及新版任务 |
| `nai_chat.py` | 系统预设、自然语言转结构化提示词、字段约束 |
| `characters.py`、`prompt_presets.py` | 人物匹配、提示词原文预设 |
| `comic_panels.py`、`nai_panels.py` | 单格记录、人物分段与请求预览 |
| `comic_editor.py` | 页格编排、台本保存、分镜包 ZIP 导出 |
| `comic_review.py` | 候选归档、上传、审批、再生成及通过图片索引 |
| `editing.py` | 选区定位与 ComfyUI 局部重绘 |
| `history_tags.py` | 历史作品标签 |
| `agent_cli.py` | 本地 Agent JSON 命令入口 |

## 一次生成如何流转

1. 用户修改输入框或选择预设；正负词分别保存，角色身份、动作和负面描述分开。
2. 若主动使用 AI 对话，服务调用所配置的文字模型，验证返回结构，再填入界面。选中的锁定预设按原文恢复；预设库本身不被改写。
3. 用户提交生图后，后端校验模型、尺寸、种子和其他参数，记录任务及提示词快照。
4. ComfyUI 走工作流渲染与任务查询；NovelAI 走请求组装、现有节流器和图片保存。
5. 完成后图片与参数留在历史。漫画工作台将候选绑定到分镜，由用户决定是否通过。
6. 微调创建新任务，不覆盖已确认图。超时或状态未知不应盲目重发，先查询已有任务。

## 数据布局

```text
data/
  config.json          # 私人服务配置和凭据，不入库
  workflows.json       # 导入工作流、映射与默认值
  characters.json      # 人物资料
  prompt_presets.json  # 分类原文预设
  panels.json          # 分镜与生成设置
  jobs.json            # 实际生成任务和参数
  comic_review.json    # 图片归档、审批事件、页目录
  generated/           # NovelAI 结果、上传图片等
  edits/               # 局部改图会话与选区
  logs/                # 运行日志
```

ComfyUI 的图片主要由其自身 output/temp/input 管理。便携版默认布局可支持本地回读；远程地址或不同安装仍需原服务在线。JSON 文件替换写入防止半截文件，但不等于整个多文件变更具备数据库事务保障。部署更新前应整体备份 `data/`。

## 编辑、审批与导出

页码和格号可重新排列，内部 ID 保持稳定。保存使用 revision 检查，防止旧页面覆盖新修改；删除分镜会解除图片绑定，图片转回待归档。通过标记对应具体任务和图片索引。

导出包含台本 JSON / Markdown、各格输入、manifest 和人工选图索引。历史单串提示词无法可靠拆成原模块时保留整串并标记来源，不虚构预设。导出索引不等于已打包所有图片，迁移设备时需同时复制图片文件。项目记忆文件只有实际存在时才纳入包。

## 安装与平台限制

标准 Python 可通过 `requirements.txt` 安装依赖启动；`launch.ps1` 和部分便携版便利功能假定父目录下存在 `python_embeded/`、`ComfyUI/`。示例 Skill 中的路径需要按安装位置调整。自动寻找项目记忆、离线图片回读及部分本机工作流仍带有原部署目录约定；其他环境需检查这些配置，尚未宣称跨平台完整验收。

## 验证

后端测试主要使用 aiohttp 测试服务器、临时 Store 和模拟上游。部分前端测试需要 Node，浏览器交互测试需要额外安装 Playwright 和浏览器，不属于 Python 运行依赖。

`tests/test_comic_editor.py` 中的外部 Skill 集成校验可通过 `COMIC_PIPELINE_SCRIPT` 指定 `panel_bundle.py`；未配置且未安装时跳过该项，其余测试照常运行。不为测试调用真实文字模型或图像服务。
