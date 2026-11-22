[English](../en/BGM.md) | **简体中文**

# BGM

`inputs/bgm/` 是**本地 BGM 输入目录**，不属于仓库内容，默认被 Git 忽略。

## 输入目录布局

```text
inputs/
  bgm/      # 背景音乐（当前管线使用）
  fonts/    # 预留：自定义字幕字体
  models/   # 预留：本地模型文件
```

当前只有 `inputs/bgm/` 被管线读取；`fonts/` 与 `models/` 是预留约定，代码尚未使用。

## 用途

`reachreel run` 会扫描 `inputs/bgm/` 里的音频，随机选一首（或用 `--bgm FILE` 指定），循环到视频长度，并在旁白和环境声出现时自动闪避。

- `inputs/bgm/` 不存在或为空时，管线不混 BGM，正常出片。
- `--bgm FILE` 指定某一首。
- `--no-bgm` 本次不混 BGM。
- 目录会在首次运行时自动创建。

## 支持格式

`.mp3` `.wav` `.m4a` `.aac` `.flac` `.ogg` `.opus`

## 命名与选择

按情绪命名，方便手动指定：

```text
inputs/bgm/
  calm-morning.mp3
  mountain-air.mp3
  river-walk.mp3
```

```bash
reachreel run video.mp4 --bgm inputs/bgm/calm-morning.mp3
```

## 仓库不附带曲目

仓库不包含任何 BGM。把你的曲目放进 `inputs/bgm/`；所有音频文件默认被 Git 忽略。如果确实要版本化某首曲子，用 `git add -f` 强制加入。

## 曲目目录

`inputs/bgm/manifest.json` 是可选的，只保存元数据，暂不影响选曲。给每首曲子补一条记录，发布前就能导出授权清单：

```json
{
  "tracks": [
    {
      "file": "calm-morning.mp3",
      "title": "Calm Morning",
      "author": "Example Artist",
      "source": "https://example.com/calm-morning",
      "license": "CC0-1.0",
      "duration": 152.3,
      "mood": ["calm", "morning"]
    }
  ]
}
```

`mix` 会把匹配的记录写进 `manifest.json` 的 `bgm_metadata`，`reachreel licenses <workdir>` 再把它渲染成 `licenses.md`。

## 授权

请使用 CC0 或明确允许商用的曲目，并在发布前逐首确认授权。没有做过独立授权审查前，不要在本仓库重新分发第三方曲目。

## 本地验证用过的曲目

以下曲目仅用于本地验证，**不随仓库分发**：

| 文件 | 曲名 | 来源 | 元数据授权 |
|---|---|---|---|
| `niagara-ambient.mp3` | Endless Ethereal Ambient Texture with Glockenspiel, Floating Pads and Water | https://archive.org/details/soundica-fs-868373-endless-ethereal-ambient-texture-with-glockenspi | CC0 1.0 |
| `ski-ukulele.mp3` | UKULELE BALLAD BUTTERFLY | https://archive.org/details/UkuleleBalladButterfly | CC0 1.0 |

Internet Archive 的元数据目前把两首曲子标为 CC0 1.0，但这不能证明上传者有权做公共领域 dedication。没有做过独立授权审查前，不要重新分发。
