"""Demo + Inject 合并插件：注入按钮 / 检测运行实例 / 叠加窗口。

文件夹名：InjectDemo
入口文件：plugin.py

演示内容（合并了原 DemoPlugin 与注入能力）：
  1. plugin_api.inject_home_button(...)  运行时向主页面注入一级按钮
  2. plugin_api.running_instances()      检测当前正在运行的 MC 实例
  3. plugin_api.make_overlay(...)         在选中的实例上叠加透明窗口
  4. 展示 plugin_api 的 API 信息

用法：
  1. 到 Plugins 页运行一次本插件；
  2. 主页面会出现注入的按钮“Injected Demo”，点击可再次运行；
  3. 窗口内下拉选择正在运行的 MC 实例，点“在选中实例上叠加窗口”
     （需先启动一个 Minecraft 实例，否则提示无实例）。
"""

NAME = "InjectDemo"
VERSION = "2.0"


def _info():
    import plugin_api
    lines = []
    lines.append("OpenMCL 根目录: " + plugin_api.base_dir())
    vs = plugin_api.get_versions()
    lines.append("已安装版本: " + (", ".join(vs) if vs else "(无)"))
    if vs:
        lines.append("第一个版本目录: " + plugin_api.version_dir(vs[0]))
    lines.append("总内存: {:.0f} MB".format(plugin_api.total_memory() / 1048576))
    lines.append("默认 Java: " + plugin_api.java_path())
    return lines


def _instance_label(inst):
    title = inst.get("title") or "(无窗口)"
    return "{}  [pid {}]".format(title, inst.get("pid"))


def main():
    import plugin_api

    print("{} v{} 启动".format(NAME, VERSION))

    # 1) 运行时向主页面注入按钮（点击后重新运行本插件）
    plugin_api.inject_home_button(
        "Injected Demo",
        message="Demo + Inject 合并插件：选择运行实例后叠加窗口。")

    # 2) 检测当前运行的 MC 实例
    insts = plugin_api.running_instances()
    print("检测到运行中的实例: {}".format(len(insts)))

    # 3) 构建主窗口
    w = plugin_api.make_window("Demo + Inject - OpenMCL", 520, 420)
    w.label("{} v{} - 注入 / 实例 / 叠加".format(NAME, VERSION))
    w.stretch()

    # API 信息
    tx = w.textarea("\n".join(_info() + ["", "实例检测 API: running_instances()"]),
                    readonly=True)
    tx.setFixedHeight(150)
    w.spacing(4)

    # 实例选择
    w.label("正在运行的 MC 实例（{} 个，可点击“刷新”重扫）".format(len(insts)))
    combo = w.combo([_instance_label(i) for i in insts], 0)

    state = {"overlay": None, "insts": insts, "combo": combo}

    def refresh():
        state["insts"] = plugin_api.running_instances()
        combo.clear()
        combo.addItems([_instance_label(i) for i in state["insts"]])

    def attach():
        insts_now = state.get("insts") or []
        if not insts_now:
            w.label("无运行中的实例，请先启动 Minecraft。").setStyleSheet("color:#ff8080;")
            return
        idx = max(0, combo.currentIndex())
        if idx >= len(insts_now):
            idx = len(insts_now) - 1
        inst = insts_now[idx]
        pid = inst.get("pid")

        ov = state.get("overlay")
        if ov is None:
            ov = plugin_api.make_overlay("InjectedDemo HUD", 300, 120)
            ov.label("OpenMCL Overlay", color="#ffffff", size=14)
            ov.spacing(2)
            ov.label("实例 pid: {}".format(pid), color="#7ee0a0", size=12)
            ov.label("实例标题: {}".format(inst.get("title") or "(未知)"),
                     color="#b0b0b8", size=11)
            ov.spacing(2)
            ov.textarea("透明叠加窗（点击穿透），不拦截游戏操控。\n"
                        "实例不在最上方时自动隐藏。", color="#c0c0c8")
            state["overlay"] = ov
        ov.bind_to_mc(pid=pid)
        # 实例不在最上方时自动隐藏（忽略叠加窗自己）
        ov.show_only_when_foreground(True)
        print("已把穿透叠加窗绑定到实例 pid={}, 前台显隐已开启".format(pid))

    def attach_clickable():
        insts_now = state.get("insts") or []
        if not insts_now:
            w.label("无运行中的实例，请先启动 Minecraft。").setStyleSheet("color:#ff8080;")
            return
        idx = max(0, combo.currentIndex())
        if idx >= len(insts_now):
            idx = len(insts_now) - 1
        inst = insts_now[idx]
        pid = inst.get("pid")

        ov = state.get("clickable_overlay")
        if ov is None:
            ov = plugin_api.make_overlay("InjectedDemo Clickable", 280, 120,
                                         clickable=True)
            ov.label("可点击叠加窗", color="#7ee0a0", size=13)
            ov.spacing(2)
            ov.label("实例 pid: {}".format(pid), color="#b0b0b8", size=11)

            def on_click():
                print("可点击叠加窗按钮被点击，当前 pid =", pid)
                label.setStyleSheet("color:#ffd680;")

            label = ov.label("这是可点击的区域", color="#d0d0d8", size=11)
            ov.spacing(2)
            ov.button("点我试试", on_click)
            state["clickable_overlay"] = ov
        ov.bind_to_mc(pid=pid)
        ov.show_only_when_foreground(True)
        print("已把可点击叠加窗绑定到实例 pid={}".format(pid))

    w.button("在选中实例上叠加窗口", attach)
    w.button("可点击叠加窗口", attach_clickable)
    w.button("刷新实例", refresh)
    w.spacing()
    w.stretch()
    w.button("关闭窗口", lambda: w.quit())
    w.run()


if __name__ == "__main__":
    main()