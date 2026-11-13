# 农夫助手（tfwr_companion）

给《The Farmer Was Replaced》用的 N.E.K.O. 陪玩插件：读文档、写代码、执行并验收。

## 反推到的事实

游戏是 Unity + Mono（appid 2060160），存档全部是纯文本，位于：

`%USERPROFILE%\AppData\LocalLow\TheFarmerWasReplaced\TheFarmerWasReplaced\`

| 文件 | 用途 |
|---|---|
| `Saves/Save0/main.py`、`f0.py`… | 玩家代码 |
| `Saves/Save0/__builtins__.py` | 内置 API 签名 + docstring（51KB） |
| `Saves/Save0/save.json` | 资源、解锁、打开的文档 |
| `output.txt` | 最近一次运行的输出 / 报错 / 警告 |
| `options.txt` | `file watcher` 等选项 |

`Core.dll` 里确认了 `Saver.StartFileWatcher / OnFileChanged / ApplyCodeChanges`：文件监视开启后，磁盘上的 `.py` 改动会被直接写进游戏内编辑器。执行本身没有对外 API，所以插件用 `Shift+F5`（停止）再 `F5`（开始）来保证每次都是全新一次运行。

## 入口

| 入口 | 作用 |
|---|---|
| `tfr_status` | 游戏状态、资源、解锁、文件监视、最近输出 |
| `tfr_docs` | `list` / `search` / `read` 内置 API 文档 |
| `tfr_code` | `list` / `read` / `write` / `watcher` |
| `tfr_run` | `run` 执行并验收 / `stop` / `check` 只读验收 |
| `tfr_watch` | `start` / `stop` / `status`：代码文件变化后自动 Shift+F5→F5 |
| `tfr_solve` | `context` 读代码/输出/存档；`apply` 写代码并等待自动执行，返回 `ran` 表示是否真的执行了 |

## 使用前提

1. 游戏内把 `file watcher` 打开（或用 `tfr_code action=watcher value=enabled`，需先关游戏）。插件只在启动时、游戏没运行且 `allow_write = true` 时自动打开一次；游戏开着启动插件的话，退出游戏后要重启插件或手动开。
2. 插件配置里 `allow_input = true`（默认），否则只能手动按 F5 再用 `tfr_run action=check`。

## 安全边界

- 写代码前备份到 `Saves/Save0/.bak/`。
- `allow_write = false` 或「游戏运行时且文件监视关闭」→ `tfr_code write` 和 `tfr_solve apply` 都拒绝写入（否则会被游戏退出时覆盖）；`allow_write = false` 时 `tfr_code watcher` 和启动时自动开 file watcher 也不写 options.txt。
- `verify_game`（`auto` = 检测到游戏安装才开）：`tfr_status` 报告安装目录和 exe 是否存在；开启时写入后回读校验。`tfr_run run` 本身就用 `output.txt` 与 `save.json` 的前后差异验收。
- 发键前用 PID 找到游戏窗口，只对游戏进程的窗口操作。

## 自检

```powershell
python tfr.py
```
