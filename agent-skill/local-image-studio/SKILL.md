---
name: local-image-studio
description: Use when a local Agent needs to generate images with 绘境 (Local Image Studio) and local ComfyUI, select an imported workflow, generate or edit image prompts, query an existing drawing task, or retrieve its images. Supports natural-language descriptions through the configured Prompt API and Agent-written prompts. Applies to 绘境本地生图、调用本地工作流、Agent生图; not to unrelated hosted image services.
---

# 绘境本地生图

通过绘境已有的本地接口调用 ComfyUI。工作流、模板、模型与 API 配置由软件管理；Agent 不需要读取 API 密钥。

本机路径：

- Python：`E:\ComfyUI_windows_portable\python_embeded\python.exe`
- CLI：`E:\ComfyUI_windows_portable\local-image-studio\agent_cli.py`
- 网页：`http://127.0.0.1:8190`

其他本地 Agent 可以复制本 skill 并按安装位置调整这两条路径。CLI 是接口的唯一实现，skill 不复制 API 客户端逻辑。

## 发现与选择

运行 CLI `start` 启动或复用绘境服务，再运行 `status` 和 `workflows`。`start` 不打开浏览器、不启动 ComfyUI；ComfyUI 未连接时使用网页的“启动本地 ComfyUI”，或明确报告连接问题。已知的本机 ComfyUI 启动命令也可以复用。

从实际返回的工作流中选择 ID。用户指定的工作流优先；未指定时，若现有基础文生图工作流适合任务，就使用它。其他图须检查变量、默认模型与字段映射；`workflows --detail` 提供完整图。多个同名图使用 ID 区分。

仅支持 API 格式 JSON 工作流。未配置变量必须先在网页中配置，或通过请求中的 `values` 提供。没有 `%prompt%` 的图需要已保存的正面提示词节点映射；不能把“提交成功”当作提示词已被正确使用。自选模型必须与工作流结构兼容。

## 提示词模式

- **自然语言 + 配置的 Prompt API**：`use_api:true`（generate 默认值）。提供 `description`，可用 `template:"tags"` 或 `template:"natural"` 选择已有模板。API 尚未配置时，指出需要在网页填写地址、模型及密钥；不从其他应用搜集凭据，不自动换成别的服务。
- **用户已有 Prompt，或明确让 Agent 自己生成/优化 Prompt**：Agent 在当前模型内编写或保留正负 Prompt，设置 `use_api:false`，提供 `positive_prompt` 和 `negative_prompt`；不执行 `prompt` 命令。用户要求保留 Prompt 时按原文传递。
- **只生成提示词**：使用 `prompt --request-file 文件`。它调用配置的 API，返回最终正负 Prompt，不提交 ComfyUI。

标签式提示词适合 SDXL / Illustrious；自然语言式可用于 Flux / Anima。遵从用户指定风格与模型。最终 Prompt 可编辑；在 `use_api:false` 模式下直接提交，不重复叠加固定提示词。

## 提交一次，查询同一任务

将请求写入 UTF-8 JSON 文件，再执行 CLI，避免把含引号或换行的文本拼接到 shell 命令中。

```json
{
  "workflow": "基础文生图 · SDXL / SD",
  "description": "雨后的城市街角，霓虹倒影，电影感光线",
  "use_api": true,
  "template": "tags",
  "values": {"width": 768, "height": 1024, "steps": 24},
  "random_seed": true
}
```

PowerShell 示例（请求文件由 Agent 创建，工作流名称可改为实际查询所得 `workflow_id`）：

```powershell
$studioPython = 'E:\ComfyUI_windows_portable\python_embeded\python.exe'
$studioCli = 'E:\ComfyUI_windows_portable\local-image-studio\agent_cli.py'
& $studioPython -X utf8 $studioCli start
& $studioPython -X utf8 $studioCli status
& $studioPython -X utf8 $studioCli workflows
& $studioPython -X utf8 $studioCli generate --request-file 'E:\ComfyUI_windows_portable\local-image-studio\request.json' --wait 45 --output 'E:\ComfyUI_windows_portable\output\studio-agent'
```

CLI 返回 JSON。保留任务 `id`；`prompt_id` 是 ComfyUI ID，两者不同。后续 `job`、`download`、`resume` 使用绘境的 `id`。

- `generate` 只提交一次；等待 45 秒后仍在排队或运行时，执行 `job 同一ID --wait 45 --output 输出目录`。
- 每次 `--wait` 最多 55 秒；查询间可报告进度。不重新执行 generate 来“等待”。
- 请求错误且没拿到 ID 时，先执行 `history` 检查已有任务和时间/描述，避免重复调用 API 和生图。
- `unknown` 且有 `prompt_id`：`resume 同一ID` 恢复查询，再执行 job。没有 `prompt_id` 时先检查 ComfyUI 队列，不能假定未提交。
- `failed`：读取 `error`，修正有证据的原因后再提交新任务。保留原描述与 Prompt。
- CLI 退出码 0 不保证生图成功。只有 `state:"done"` 且 `images` 非空，才报告成功。

复现时保留工作流与实际 `values.seed`，设置 `random_seed:false`。

## 读取与交付

成功时 `--output` 下载图片并返回 `files` 的绝对路径。已完成的任务也可执行：

```powershell
& $studioPython -X utf8 $studioCli download '绘境任务ID' --output 'E:\ComfyUI_windows_portable\output\studio-agent'
```

检查返回文件存在，必要时查看图片确认画面。交付时展示实际图片并说明工作流、实际种子和路径。例如 `![生成图片](E:/ComfyUI_windows_portable/output/studio-agent/实际文件名.png)`；不要用不存在的示例路径，也不要只回报“已提交”。

## 半自动局部改图

使用固定的 SAM 选区 + WAI Illustrious SDXL v170 工作流，无须提供 workflow_id。只修改选区及其柔化边缘。已有文生图或 Krea 图生图不能代替这个流程。

1. `edit-upload --image 原图绝对路径` 返回会话 `id`、原图 `width`/`height` 和 `image_url`。后续请求将这个 ID 写入 `edit_id`。原图限制 20 MB、单边 4096 像素。服务接收图片内容，不接受任意本地路径。
2. `edit-select --request-file 选区请求.json --wait 45 --output 选区目录` 提交 SAM 一次。请求如下；坐标使用上传后原图像素，从左上角开始，不能使用缩略图显示坐标。包含/排除点各最多 40 个；`threshold` 范围 0.05–0.99。文字部位建议简短英文。有文字时点选用于筛选候选区域；无文字时至少给一个包含点。

```json
{"edit_id":"上传返回的会话ID","region":"earrings","positive_coords":[{"x":618,"y":291}],"negative_coords":[],"threshold":0.5}
```

3. 保存返回的绘境任务 `id`，作为 `mask_job_id`；它不是 edit_id 或 ComfyUI prompt_id。未完成用 `job 同一任务ID --wait 45 --output 选区目录`。只有 `state:done`、`mask_ready:true` 才表示选区已验证。下载图是黑白选区，白色会重绘；与原图对照检查，确认覆盖正确部位且未覆盖其他角色。识别不到或选区错误时调整描述/点选后显式提交新选区；不能把空选区当成功。
4. 可选 `edit-prompt --request-file 改图描述.json`，请求含 `edit_id`、`description`、`preserve`，可加 `region`。配置的 Prompt API 会收到原图，须支持视觉输入；返回正负提示词，不提交重绘。也可由 Agent 自己写 Illustrious 标签，跳过此命令；例如 `blue whale-shaped earrings, blue hair, watercolor illustration`。不要静默换用别的 API。
5. 查看选区正确后 `edit-render --request-file 重绘请求.json --wait 45 --output 结果目录`。请求使用最新 mask_job_id，`confirmed:true` 表示调用者已检查选区。不要在未查看选区前直接确认。

```json
{"edit_id":"会话ID","mask_job_id":"最新选区任务ID","confirmed":true,"description":"把DS耳饰改成蓝色鲸鱼形状","preserve":"蓝色头发、鲸鱼耳朵、表情和原画风","positive_prompt":"blue whale-shaped earrings, blue hair, watercolor illustration","negative_prompt":"blurry, low quality","denoise":0.5,"steps":20,"cfg":6,"grow":4,"blur":3,"seed":12345}
```

参数范围：denoise 0.05–1、steps 1–60、cfg 1–15、grow 0–64、blur 0–32。seed 可省略随机。返回新的绘境重绘任务 ID；继续查询和下载都用这个 ID。提示词和原图不能保证选区内所有细节不变，最终检查画面。

必填字段：select 的 edit_id；region 或 positive_coords 至少一个非空。prompt 的 edit_id、description，返回 positive_prompt/negative_prompt；preserve/region 可选。render 的 edit_id、mask_job_id、confirmed:true、positive_prompt；negative_prompt/description/preserve 可选。参数默认值为 threshold=0.5、denoise=0.5、steps=20、cfg=6、grow=4、blur=3，点选默认空数组。

选择或重绘超时不重复提交，用 history 找到同 edit_id/edit_purpose 的任务，再 job 查询；unknown 有 prompt_id 时用 resume。`edit-session 会话ID` 返回 latest_job_id、selection_job_id、render_job_id 和原图地址，可供恢复。上传不会调用 Prompt API 或执行 ComfyUI；若上传响应丢失且没有拿到会话 ID，可重新上传，得到新的会话，不会重复生图。浏览器的改图页也支持原图、绿色选区覆盖、包含/排除点和结果对比。

## 快速命令表

| 命令 | 结果 |
| --- | --- |
| `start` | 启动或复用绘境服务 |
| `status` | API 配置就绪情况和 ComfyUI 连接 |
| `workflows --detail` | 图、变量、默认值、映射 |
| `prompt --request-file 文件` | 只调用 API 生成最终 Prompt |
| `generate --request-file 文件 --wait 45` | 提交一次并短暂等待 |
| `job ID --wait 45 --output 目录` | 查询已有任务、成功后下载 |
| `history` | 获取历史状态及绘境任务 ID |
| `resume ID` | 继续查询未知任务 |
| `download ID --output 目录` | 下载成功任务图片 |
| `edit-upload --image 图片路径` | 上传改图原图，返回会话 ID |
| `edit-session 会话ID` | 读取最新选区/重绘任务 ID |
| `edit-select --request-file 文件 --wait 45 --output 目录` | 提交 SAM 选区并下载黑白预览 |
| `edit-prompt --request-file 文件` | 结合原图生成改图提示词 |
| `edit-render --request-file 文件 --wait 45 --output 目录` | 用已检查的最新选区提交 WAI 局部重绘 |

请求超时不自动重试；不清空 ComfyUI 队列，不全局打断其他客户端任务。工作流导入与高级配置的详细说明见程序目录 README.md。
