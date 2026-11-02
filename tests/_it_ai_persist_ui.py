# -*- coding: utf-8 -*-
"""AI 对话坞跨页持久化回归（Edge headless，打真实运行中的平台）。

为什么需要这个测试：
    用户反复报「AI 工作台的问答，切到别的模块再回来就没了」，修过几次又回归。
    2026-11-02 查明根因不在存储本身，而在**恢复时机**：历史恢复原本写在
    `AI._ensureCtx()` 里、且排在 `await fetch('/api/ai/context')` 成功之后。
    平台在跑重活（18 个数据集的 fastp/kvsuite）时这个接口会排队或超时，
    fetch 一抛异常就整段跳过恢复 —— 页面看起来空得干干净净，localStorage
    里的消息其实一条没丢。于是「有时候正常、有时候又没了」= 取决于服务端忙不忙。

本测试守住四条线：
    ① 正常换页：会话 id 不变，消息照旧渲染；
    ② 会话 id 换掉（新标签页 / 隐私模式 / sessionStorage 被清）：从镜像键
       `vp_ai_msgs:__last` 恢复，并把会话 id 认回来；
    ③ 只写进内存、没走 2s 定时器就离开页面：pagehide 同步落盘兜住；
    ④ `/api/ai/context` 直接失败：历史仍然恢复（恢复不依赖网络）。

跑法：python tests/_it_ai_persist_ui.py
      需要平台在 127.0.0.1:8765 运行、本机有 Edge 与 selenium；缺浏览器则 SKIP。
"""
import json
import sys
import time

# 批量跑（tests/_run_all.py）时子进程 stdout 是 GBK，打 ✔/✘ 会 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:      # noqa: BLE001
    pass

BASE = 'http://127.0.0.1:8765'
FAILS = []


def check(ok, msg):
    print(('  ✔ ' if ok else '  ✘ ') + msg)
    if not ok:
        FAILS.append(msg)


def main():
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except Exception as e:                       # noqa: BLE001
        print('SKIP: 没有 selenium（%s）' % e)
        return 0

    opt = Options()
    opt.add_argument('--headless=new')
    opt.add_argument('--disable-gpu')
    opt.add_argument('--window-size=1400,900')
    try:
        d = webdriver.Edge(options=opt)
    except Exception as e:                       # noqa: BLE001
        print('SKIP: 没有可用的 Edge（%s）' % e)
        return 0

    def js(code):
        return d.execute_script("return (function(){" + code + "})();")

    try:
        d.get(BASE + '/?t=' + str(int(time.time())))
        time.sleep(3)

        # ① 正常换页
        js("AI.messages = [{role:'user',content:'R1-Q'},"
           "{role:'assistant',content:'R1-A'}]; AI._saveHistory(); return 1;")
        d.get(BASE + '/tools'); time.sleep(1.5)
        d.get(BASE + '/'); time.sleep(3)
        st = js("return {n: AI.messages.length,"
                "t: (document.getElementById('aiMsgs')||{}).innerText||''};")
        check(st['n'] == 2 and 'R1-Q' in st['t'],
              '① 换页后对话仍在: ' + json.dumps(st, ensure_ascii=False)[:120])

        # ② 镜像兜底：删掉当前会话键、换会话 id（等价于新标签页）
        js("localStorage.removeItem(AI.msgsKey());"
           "AI.convId = 'cNEWTAB'; sessionStorage.setItem('vp_ai_conv','cNEWTAB');"
           "AI._restored = undefined; AI.messages = [];"
           "document.getElementById('aiMsgs').innerHTML = ''; return 1;")
        d.get(BASE + '/?t=' + str(int(time.time())))
        time.sleep(3)
        st = js("return {conv: AI.convId, n: AI.messages.length,"
                "t: (document.getElementById('aiMsgs')||{}).innerText||''};")
        check(st['n'] == 2 and 'R1-Q' in st['t'],
              '② 换会话 id 仍能从镜像恢复: ' + json.dumps(st, ensure_ascii=False)[:140])

        # ③ pagehide 落盘：只进内存、不等 2s 定时器就跳走
        js("AI.messages.push({role:'user',content:'R3-PAGEHIDE'});"
           "localStorage.removeItem(AI.msgsKey()); return 1;")
        d.get(BASE + '/samples'); time.sleep(1.5)
        d.get(BASE + '/'); time.sleep(3)
        t = js("return (document.getElementById('aiMsgs')||{}).innerText||'';")
        check('R3-PAGEHIDE' in t, '③ pagehide 强制落盘')

        # ④ 上下文接口失败：恢复不能依赖它
        js("AI.messages = [{role:'user',content:'R4-OFFLINE'}];"
           "localStorage.removeItem(AI.msgsKey());"
           "localStorage.setItem('vp_ai_msgs:__last', JSON.stringify({t:Date.now(),"
           "conv:AI.convId, msgs:[{role:'user',content:'R4-OFFLINE'}]}));"
           "AI._restored = undefined; AI.messages = [];"
           "document.getElementById('aiMsgs').innerHTML = '';"
           "AI.ctxLoaded = false;"
           "window.fetch = function(){return Promise.reject(new Error('offline'));};"
           "return 1;")
        js("AI._ensureCtx(); return 1;")
        time.sleep(2)
        t = js("return (document.getElementById('aiMsgs')||{}).innerText||'';")
        check('R4-OFFLINE' in t, '④ 上下文接口失败仍恢复历史')

        # ⑤ 清空之后换页不能「复活」（镜像键也必须一起清）
        js("window.confirm = function(){return true;};"
           "AI.messages = [{role:'user',content:'R5-CLEARED'}];"
           "AI._saveHistory(); AI.clear(); return 1;")
        d.get(BASE + '/?t=' + str(int(time.time())))
        time.sleep(3)
        st = js("return {n: AI.messages.length,"
                "t: (document.getElementById('aiMsgs')||{}).innerText||''};")
        check(st['n'] == 0 and 'R5-CLEARED' not in st['t'],
              '⑤ 清空后换页不复活: ' + json.dumps(st, ensure_ascii=False)[:100])

        # 清掉测试痕迹，别污染用户自己的对话
        js("localStorage.removeItem('vp_ai_msgs:__last');"
           "Object.keys(localStorage).forEach(function(k){"
           "if (k.indexOf('vp_ai_msgs:')===0) localStorage.removeItem(k);});"
           "AI._restored = undefined; AI.messages = []; return 1;")
    finally:
        d.quit()

    if FAILS:
        print('FAILED: %d 项' % len(FAILS))
        return 1
    print('AI 对话坞跨页持久化 5 项通过 ✔')
    return 0


if __name__ == '__main__':
    sys.exit(main())

