# OpenMCL 插件开发指南

插件位于 OpenMCL 的 **Plugins 页面**。每个插件是一个**以 `plugin.py` 为入口的目录**（也支持 `main.py` / `__main__.py`），放在插件根目录下即可被扫描并显示在插件列表里。

插件在运行时通过 `import plugin_api` 使用 OpenMCL 提供的 API。本文档说明常用 API 与新加入的**键盘 / 鼠标控制 API**。

---

## 1. 快速开始

```python
# plugin.py
import plugin_api as api

def main():
    w = api.make_window("我的插件", 420, 300)
    w.label("Hello OpenMCL")
    w.button("点我", lambda: print("clicked"))
    w.run()          # 显示窗口并进入事件循环

if __name__ == "__main__":
    main()
```

打包后的主程序仍会在内部解释器里运行插件，因此插件只需把 `plugin.py`（连同本目录）放到插件根目录；**无需安装 PyQt5**，`plugin_api` 会自动使用主程序的 Qt。

---

## 2. 路径与配置

| 函数 | 说明 |
| --- | --- |
| `api.base_dir()` | OpenMCL 数据根目录（`~/.openmcl`） |
| `api.config_file()` | 全局配置文件路径 |
| `api.versions_dir()` | 已安装版本目录 |
| `api.assets_dir()` | 全局资源目录 |
| `api.libraries_dir()` | 依赖库目录 |
| `api.servers_dir()` | MC 服务端目录 |
| `api.plugins_dir()` | 插件根目录 |
| `api.get_versions()` | 已安装版本号列表 |
| `api.version_dir(id)` / `api.version_jar(id)` / `api.version_json(id)` | 某版本相关路径 |
| `api.config()` | 读取全局配置（dict） |
| `api.get_config(key, default)` | 读取单个配置键 |

---

## 3. 运行时注入

```python
api.inject_home_button("按钮文字", entry="plugin.py", message="提示")
```

向**主页面注入一个一级按钮**，点击后以独立进程启动 `entry`（相对插件根目录，默认自动推断当前插件入口，即点击后重新运行本插件）。

---

## 4. 实例与窗口

### 4.1 检测运行中的 MC 实例

```python
insts = api.running_instances()
# 每项： {pid, name, exe, title, hwnd}
```

- `pid`：进程号
- `title`：窗口标题（未找到窗口为空串）
- `hwnd`：窗口句柄（Windows 下找到窗口才有，否则 `None`）

### 4.2 简易窗口

```python
w = api.make_window("标题", 420, 320)
w.label("文本")
w.input("默认值")
w.textarea("多行", readonly=False)
w.button("按钮", on_click=fn)
w.checkbox("勾选框", checked=True)
w.combo(["a", "b"], current=0)
w.slider(0, 100, 50)
w.horizontal([widget1, widget2])   # 一行横向布局
w.spacing(6)
w.stretch()
w.run()      # 阻塞式运行
```

控件方法会返回创建的 Qt 控件，可直接调用 Qt 方法（如 `setFixedHeight`）。

### 4.3 绑定窗口的叠加窗（HUD）

```python
ov = api.make_overlay("HUD", 300, 120, clickable=False)
ov.label("信息", color="#ffffff", size=13)
ov.textarea("说明")
ov.button("按钮", on_click=fn)   # 需 clickable=True
ov.bind_to_mc(pid=pid, follow=True)          # 跟随 MC 窗口位置大小
ov.show_only_when_foreground(True)           # 游戏不在前台时自动隐藏
```

- `clickable=False`（默认）：鼠标点击穿透，不拦截游戏操作；
- `clickable=True`：可放按钮 / 输入框交互控件。

---

## 5. 键盘 / 鼠标控制（仅 MC 窗口内有效，Windows）

新增的键鼠注入 API。**通过 Win32 消息直接投递给目标 Minecraft 窗口**，因此：

- 键鼠事件**只被该游戏窗口接收**，不影响桌面和其它程序；
- 默认 `foreground_only=True`：**仅当目标窗口处于前台时才真正注入**，否则忽略并返回 `False` —— 这是“仅 MC 内有效”的保证；
- 非 Windows / 找不到窗口时，所有操作**安全返回 `False`**，不会抛异常。

### 5.1 创建控制器

```python
insts = api.running_instances()
if not insts:
    print("请先启动 Minecraft")
else:
    inp = api.make_input(pid=insts[0]["pid"], foreground_only=True)
```

参数：
- `pid` 或 `hwnd`：二选一，目标 MC 窗口；
- `foreground_only=True`（默认）：仅前台注入；设为 `False` 可后台注入（对部分游戏无效）。

### 5.2 键盘

| 方法 | 说明 |
| --- | --- |
| `inp.tap("w")` | 按下并抬起（一次敲击） |
| `inp.key_down("ctrl")` | 按住不放 |
| `inp.key_up("ctrl")` | 松开 |
| `inp.key("shift", down=True)` | 通用；`down=None` 等价敲击 |
| `inp.text("hello")` | 一次性输入一串文本 |

支持的按键写法：
- 单个字母 / 数字：`"w"`、`"5"`；
- 功能键：`"f1"` … `"f24"`；
- 命名键：`enter`、`esc`、`space`、`tab`、`backspace`、`shift`、`ctrl`、`alt`、`up`/`down`/`left`/`right`、`home`、`end`、`pageup`、`pagedown`、`insert`、`delete`、`minus`、`equal`、`comma`、`period`、`slash`、`backslash`、`quote`、`semicolon`、`lbracket`、`rbracket`、`grave` 等。

### 5.3 鼠标

| 方法 | 说明 |
| --- | --- |
| `inp.move(x, y)` | 把鼠标移到窗口**客户端坐标** `(x, y)`（相对窗口左上角） |
| `inp.click(x, y, button="left", hold_ms=0)` | 在坐标处点击；不传坐标则当前位置 | 
| `inp.click(button="right")` | 右键 |
| `inp.mouse_down(button="left")` / `inp.mouse_up(button="left")` | 按下 / 抬起 |
| `inp.scroll(120)` | 滚轮，正向上 / 负向下，一格约 ±120 |

支持按钮：`left` / `right` / `middle`。

### 5.4 完整示例：游戏内走动并前进

```python
import time
import plugin_api as api

insts = api.running_instances()
if not insts:
    print("没有运行中的 MC 实例")
else:
    inp = api.make_input(pid=insts[0]["pid"], foreground_only=True)
    inp.tap("e")                 # 打开背包（仅窗口在前台时生效）
    time.sleep(0.2)
    inp.key_down("w")            # 按住前进
    time.sleep(1.0)
    inp.key_up("w")
    inp.click(button="right")    # 右键
```

> 提示：对 **Java 版（LWJGL/GLFW）** 游戏，`PostMessage` 注入按键/鼠标消息通常有效；个别**全屏独占**模式可能接收不到，可改用窗口模式运行游戏。注入是否生效还取决于游戏是否处于前台捕获输入状态。

---

## 6. 其它 API

| 函数 | 说明 |
| --- | --- |
| `api.total_memory()` / `available_memory()` / `used_memory()` | 系统内存（字节） |
| `api.process_memory(pid)` | 指定进程占用内存 |
| `api.find_java()` / `api.java_path()` | 查找 Java |

---

## 7. 注意事项

- 插件代码出错不会让主程序崩溃；插件运行异常会写入 `~\.openmcl\crash.log`（标记 `CRASH (plugin)`），便于排查。
- 键鼠注入目前仅 **Windows** 平台有效；其它平台对应操作返回 `False`。
- 关闭插件窗口会清理其控件与定时器，避免后台资源累积。
- 请只在自己启动的 Minecraft 实例上测试键鼠注入，避免误操作。