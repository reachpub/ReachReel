# Glossary / 术语表

> 本术语表有意保持中英对照，是术语映射的唯一来源；不要拆成两份。  
> This glossary is intentionally bilingual and is the single source of truth for term mapping; do not split it into two files.

翻译与写作时的统一用词。**左列为英文源词（代码里的名字），中间为中文，右列为「不要译成」。**

原则：

1. **代码里的标识符不翻译**，文档里直接写 `workdir`、`manifest.json`、`script.json`。
2. 一个英文词只对应一个中文说法，避免同义漂移（stage 一律「阶段」，不要时而「步骤」「环节」）。
3. 中文主体里夹英文术语是正常的，不要硬翻（`QA` 不译成「质量保证」）。
4. 新增术语先在这里登记，再进文档。

---

## 1. 管线与产物

| English | 中文 | 说明 / 不要译成 |
|---|---|---|
| pipeline | 流水线 | 不译「管道」「管线」 |
| stage | 阶段 | 不译「步骤」「环节」「步骤条」 |
| probe | 读取视频 | `probe_video`；不要译成「探测」（太技术） |
| scene / scenes | 镜头 / 镜头列表 | `scenes.json`；不译「场景」（场景易与 set/scene 混淆） |
| scene detection | 镜头检测 | 不译「场景分割」 |
| keyframe | 关键帧 | — |
| workdir | 工作目录 | 文档里保留 `workdir` 原词 |
| manifest | manifest | 不译「清单」（`manifest.json` 是文件名） |
| artifact | 中间产物 | — |
| resume | 复用 / 续跑 | 视语境：缓存复用→「复用」；任务恢复→「续跑」 |
| force | 强制重做 | CLI 的 `--force` |
| dry run | dry run | 不译「空跑」「试运行」 |
| stage command | 阶段命令 | `reachreel script/tts/...` |
| local-only / fully local | 全本地 | 卖点用语，不要译「本地化」 |
| out of the box | 开箱可用 | — |

## 2. 模型与内容

| English | 中文 | 说明 / 不要译成 |
|---|---|---|
| VLM | VLM | 视觉语言模型，不译「多模态模型」 |
| LLM | LLM | — |
| narration | 旁白 | 不译「解说词」（本项目文案统一叫旁白） |
| script | 解说词 / 文案 | `script.json`；界面里可用「文案」 |
| segment | 段落 / 段 | `NarrationSegment`；字幕语境下用「条目」 |
| cue | 字幕条目 | SRT 的 cue |
| brief | 背景信息 | CLI 的 `--brief`；不译「简介」「摘要」 |
| style | 风格 | — |
| density | 旁白密度 | `narration_density` |
| voice | 音色 | 不译「声音」「嗓音」 |
| TTS | 配音 | 名词场景用「TTS」，动作场景用「配音」 |
| ASR | ASR | 不译「语音识别」（除首次出现需解释） |
| timestamp | 时间戳 | 不译「时间点」（时间点是时刻，时间戳是数据） |
| speech window | 语音边界 | whisper 得到的起止点 |
| chars per second (cps) | 字/秒 | **中文专用**；英文用 `words per second` |
| words per second | 词/秒 | 英文文案的时长估算 |
| word wrap | 词级换行 | 英文断行；不要用「自动换行」 |
| prompt | 提示词 | 不译「prompt 模板」以外的说法 |
| prompt pack | 提示词包 | 按语言分目录的提示词 |

## 3. 音频与画面

| English | 中文 | 说明 / 不要译成 |
|---|---|---|
| BGM | BGM | 不译「背景音乐」以外的说法；首次出现可注「背景音乐」 |
| ducking | 闪避 | 不译「压低」「闪避处理」 |
| sidechain compress | 侧链压缩 | 技术描述用；用户文档用「闪避」 |
| ambient | 原声 | 原片环境声；不译「氛围声」 |
| loudness / LUFS | 响度 | 保留 LUFS |
| true peak | 真峰值 | — |
| clipping | 削波 | 不译「爆音」 |
| burned-in subtitles | 硬字幕 | 不译「烧录字幕」（可注） |
| soft subtitles | 软字幕 | 字幕轨 |
| safe area / margin | 安全边距 | — |
| overflow | 超框 | 字幕像素超框 |
| letterbox / pillarbox | 黑边 | — |
| rotation metadata | 旋转元数据 | — |
| display size | 显示分辨率 | 与编码分辨率区分 |

## 4. 交付与质量

| English | 中文 | 说明 / 不要译成 |
|---|---|---|
| QA gate | QA 闸门 | 不译「质量关卡」 |
| pass / warn / fail | 通过 / 警告 / 失败 | — |
| threshold | 阈值 | 不译「门限」 |
| strict mode | 严格模式 | `--strict` |
| review | 审校 | `review.md`；不译「审核」 |
| render | 渲染 | — |
| master | 母版 | 多平台导出时的成片版本 |
| preset | 预设 | — |
| job | 任务 | `jobs.py`；不译「作业」 |
| queue | 队列 | — |
| worker | worker | 保留原词（单 worker 串行队列） |
| ETA | 预计剩余时间 | 界面可缩写 ETA |
| reveal in Finder | 在访达中显示 | Windows: 在资源管理器中显示 |

## 5. 工程与平台

| English | 中文 | 说明 / 不要译成 |
|---|---|---|
| backend / engine | 引擎 | `engines/`；区分 backend（推理后端）与 provider（服务商） |
| provider | provider | 不译「提供者」「服务商」（首次可注） |
| capability | 能力 | — |
| registry | 注册表 | 引擎注册表 |
| local-first | 本地优先 | — |
| remote mode | 远程模式 | — |
| fallback | 兜底 / 回退 | 引擎降级用「兜底」，错误重试用「回退」 |
| extras | extras | Python 可选依赖组 |
| sidecar | sidecar | 打包外壳附带的进程 |
| notarization | 公证 | 不译「公证化」 |
| codesign | 签名 | — |
| entitlements | 权限声明 | — |
| i18n | 国际化 | 保留 i18n |
| locale | 语言 / 区域 | `zh-CN`、`en` |
| catalog | 语言包 | `locales/*.toml` |
| content language | 成片语言 | 与界面语言区分 |
| UI language | 界面语言 | — |

---

## 6. 语言相关字段对照（配置与代码）

| 代码 | 中文文档写法 | 备注 |
|---|---|---|
| `content.language` | 成片语言 | `zh` / `en` |
| `ui_language` | 界面语言 | 与成片语言独立 |
| `speech_chars_per_second` | 字/秒（中文） | 英文见语言档案的 `words per second` |
| `subtitle_max_chars` | 每行字数 | 中文按字；英文按词换行后按字符校验 |
| `subtitle_max_lines` | 每行行数上限 | — |
| `PROMPT_VERSION` | 提示词版本 | 缓存键组成部分，改提示词必须升版本 |
