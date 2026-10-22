# 使用指南

## 提示词与预设

NovelAI 页面按画风、任务、场景、动作拆开正负输入，组合预览用于检查最终内容。模块优先级调整拼接顺序，不代表替换 NovelAI 的权重语法。人物可添加最多 5 个独立卡片，外形、动作追加词和负面词分别编辑。清空按钮只作用于对应输入。

从预设选择原文后可以继续修改本次内容；保存到预设库才会改变库中的内容。不要把 ComfyUI 特定模型的权重语法与 NovelAI 语法混用。

## AI 对话

NovelAI 生图和漫画编辑的生成栏提供 AI 对话。打开 API／系统预设配置文字服务，描述镜头后选择发送并填入；检查结果，不满意可继续对话或撤销。API 连接与既有 Prompt API 共用。若请求期间手工修改输入，回复应作为待处理结果，避免覆盖手动修改。

## 漫画编辑站

按作品、话、台本版本和页定位分镜。编辑台本可以增加下一页、移动或删除格、修改对白和镜头说明。保存后再生成图片；历史已生成版本仍保留。

每格可上传已有图或从待归档列表绑定。候选可以比较、通过或修改参数生成新版。上传图没有原始生成参数时，不会凭空补齐 Prompt 或 Seed。审批后导出 Skill 分镜包及选图记录，交给外部排版工具。

## 历史与备份

按日期、作品标签筛选创作历史。停止服务后整体备份 `data/`，同时备份 ComfyUI 的输出目录。更新程序只替换代码，不用仓库空数据覆盖个人数据。

## Agent 调用

```powershell
python agent_cli.py status
python agent_cli.py workflows
python agent_cli.py history
python agent_cli.py generate --request-file request.json --wait 45
python agent_cli.py job TASK_ID --wait 45 --output ./downloads
```

请求用 UTF-8 JSON 文件传递，避免 shell 破坏特殊符号。Agent 自己写好的提示词应设置 `use_api:false`，防止再经文字服务改写。未知状态先查询原任务，不重复生成。更多命令见 `python agent_cli.py --help` 及 `agent-skill/`。
