# -*- coding: utf-8 -*-
"""MEGAHIT 驱动包装：用平台自己的解释器执行 MEGAHIT 自带的 bin/megahit.py。

为什么需要它（2026-10-29）：分发包**不带 3rd/python**，而
`3rd/tools/megahit-*/megahit.bat` 要找一个 python.exe 才能跑它的驱动
（bat 里没有就 exit 9009）。MEGAHIT 的驱动只依赖标准库，真正的计算在
同目录的 megahit_core.exe 里，所以直接在本进程里 runpy 执行它即可：

  冻结版：VirusPlatform.exe --run-engine megahit_engine.py -1 R1 -2 R2 -o OUT ...
  源码版：python -u Virus_Platform_Core/megahit_engine.py <同样的 megahit 参数>

命令行与官方 megahit 完全一致，参数解析/日志/退出码都由驱动自己做。
驱动用 `os.path.dirname(os.path.realpath(__file__))` 定位 megahit_core.exe，
run_path() 会把 __file__ 设成驱动路径，所以不需要额外设置。
"""
import builtins
import os
import runpy
import sys


def _get_config():
    """兼容两种执行方式：包内模块（--run-engine）与直接脚本（源码模式）。

    源码模式 engine_cmd() 用 `python -u Virus_Platform_Core/megahit_engine.py`，
    此时 sys.path[0] 是包目录，`from Virus_Platform_Core...` 不可见；补上
    平台根目录再导入。冻结模式走 run_module，正常绝对导入即可。
    """
    try:
        from Virus_Platform_Core.config import get_config
        return get_config
    except ImportError:
        pass
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from Virus_Platform_Core.config import get_config
    return get_config


def megahit_driver_path():
    """从工具注册里拿 megahit.bat，再定位同目录的 bin/megahit.py。"""
    bat = _get_config()().tool('megahit')
    if not bat:
        raise SystemExit('未检测到 MEGAHIT（3rd/tools/megahit*/megahit.bat）')
    drv = os.path.join(os.path.dirname(os.path.abspath(bat)), 'bin',
                       'megahit.py')
    if not os.path.isfile(drv):
        raise SystemExit('MEGAHIT 驱动缺失: ' + drv)
    return drv


def _install_exit_builtins():
    """MEGAHIT 驱动里直接写 `exit(0)`（依赖 site 注入的内建名）。

    PyInstaller 冻结环境不跑 site，`exit` / `quit` 不存在 → 驱动在
    `--version`、正常结束等路径上抛 NameError（实测：输出正确但退出带
    异常）。这里补上，语义与标准解释器一致（等价 sys.exit）。
    """
    if not hasattr(builtins, 'exit'):
        builtins.exit = sys.exit
    if not hasattr(builtins, 'quit'):
        builtins.quit = sys.exit


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _install_exit_builtins()
    drv = megahit_driver_path()
    sys.argv = [drv] + argv
    runpy.run_path(drv, run_name='__main__')
    return 0


if __name__ == '__main__':
    try:
        rc = main()
    except SystemExit as e:                     # 驱动的 sys.exit(rc) 原样透传
        rc = e.code if isinstance(e.code, int) else (1 if e.code else 0)
    sys.exit(rc)
