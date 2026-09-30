"""Export timed browser frames as a captioned 4m20s replay, not a live recording."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image

from chapter6_demo.v12_2.common import file_sha, save_json


def export(output, evaluate, call, ffmpeg):
    output = Path(output)
    output.mkdir(parents=True)
    scenes = [
        (15, "setView('history');frameIndex=0;renderFrame()", "01 · 研究对象是跨实例规则", "历史 v1.2 r2 真实日志回放。外层改进程序，内层才执行 TSP 路线构造；不是一次路线生成。"),
        (15, "frameIndex=0;renderFrame()", "共同初始化与有限预算", "三条相同手写规则、8 个提案时隙。A 维护输出代表，B 保存开发机会，M 记录已执行反馈。"),
        (15, "frameIndex=1;renderFrame()", "第一次候选改进并入池", "p3 改善父代与当时全局 validation 最好值。合格改进进入 B，获得两次有限机会。"),
        (15, "frameIndex=2;renderFrame()", "兑现一次分支开发", "p3 被实际选择为父代；p4 再次改善。p3 的剩余额度减少，新候选的准入独立判断。"),
        (15, "frameIndex=3;renderFrame()", "成功改进链继续增长", "p5 的改进使成功深度达到 3。展示的是已归档执行结果，不是预测未来会成功。"),
        (15, "frameIndex=4;renderFrame()", "一次续开发没有改善", "p6 未优于父代，仍然消耗开发机会。尝试深度与成功改进深度必须分开记录。"),
        (15, "frameIndex=5;renderFrame()", "新行为也可能质量不足", "p7 来自一次重启，质量明显较差。程序或行为不同本身不足以获得新的开发额度。"),
        (15, "frameIndex=8;renderFrame()", "预算用尽，按 validation 冻结", "这条历史运行有成功也有失败。最终测试只评价已冻结规则，不反过来指导搜索。"),
        (15, "frameIndex=8;renderFrame();document.querySelector('details.panel').open=true", "不能用单条成功轨迹代表整组效果", "全部 18 次可切换。旧固定开发平均 Test gap 为 5.569%，旧关系开发为 6.379%；旧版普通调度和 W 存在混杂。"),
        (15, "setView('synthetic');caseIndex=0;renderSynthetic()", "02 · 合成控制用例，零模型调用", "现在使用人工设定的历史反馈。两分支的局部收益均为 0.01、使用次数均为 0，只改变已观察族证据。"),
        (15, "caseIndex=0;renderSynthetic()", "同收益下，族证据改变选择", "local_distance 额外有两次失败。FIFO 选择 p2，完整排序选择 p3；数值由真实 V121 控制器计算。"),
        (15, "caseIndex=1;renderSynthetic()", "交换失败归属，完整排序改选 p2", "变化来自证据对应关系，不是固定偏好某个程序编号。这只能说明排序规则能够使用该信息。"),
        (15, "caseIndex=2;renderSynthetic()", "样本不足时回退共享统计", "两候选族都没有足够独立观察，使用相同 q。当前同收益用例中，两种排序选择一致。"),
        (15, "caseIndex=0;renderSynthetic()", "无效子代仍然扣除开发额度", "表中随后注入的是预先规定的无效反馈。没有新成功深度，不补额度，也没有声称任何性能提升。"),
        (15, "caseIndex=0;renderSynthetic()", "从机制响应到实际价值还有距离", "真实搜索必须出现多个可选分支、可区分证据和选择差异，之后才可能检验是否带来独立测试收益。"),
        (15, "setView(DATA.current?'current':'history');frameIndex=0;renderFrame()", "03 · 本轮真实筛查与证据边界", "v1.2.3 使用 MiniMax M3、两控制器、八个配对区块。完整保留无效提案与未入池运行，不挑选成功案例。"),
        (20, "setView(DATA.current?'current':'history');frameIndex=0;renderFrame()", "开题基础成立，方法有效性继续检验", "程序合法性、开发机制、排序增量与多算法集合用途是不同问题。后续四组归因和独立选择器实验均需另行冻结。"),
    ]
    assert sum(s[0] for s in scenes) == 260
    call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1080,
                                                 "deviceScaleFactor": 1, "mobile": False})
    # Raw RGB frames are piped directly; this is a timed visual walkthrough,
    # not wall-clock footage of API calls. Single-thread encode avoids burst load.
    target = output / "chapter6_demo_4m20s.mp4"
    process = subprocess.Popen([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1440x1080", "-r", "1", "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22",
        "-r", "15", "-threads", "1", "-movflags", "+faststart", str(target)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    records, subtitles, elapsed = [], [], 0
    try:
        for idx, (seconds, action, title, caption) in enumerate(scenes):
            evaluate(action + ";window.scrollTo(0,0)")
            overlay = """(()=>{let el=document.getElementById('video-caption');if(!el){el=document.createElement('div');el.id='video-caption';el.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:9999;background:#102e3ef5;color:#edf7f4;padding:14px 36px 18px;border-top:3px solid #64bba9;font:17px/1.6 Microsoft YaHei,Segoe UI,sans-serif';document.body.appendChild(el);}el.textContent='';const small=document.createElement('div');small.style.cssText='font-size:11px;letter-spacing:1px;color:#97cbc3';small.textContent='定时画面演示 · 离线回放 · 无现场模型调用';const strong=document.createElement('strong');strong.textContent=TITLE;const text=document.createElement('div');text.textContent=CAPTION;el.append(small,strong,text);})()"""
            evaluate(overlay.replace("TITLE", json.dumps(title, ensure_ascii=False)).replace("CAPTION", json.dumps(caption, ensure_ascii=False)))
            raw = base64.b64decode(call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})["data"])
            rgb = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")).tobytes()
            for _ in range(seconds):
                process.stdin.write(rgb)
            records.append({"index": idx+1, "start_seconds": elapsed, "duration_seconds": seconds,
                            "title": title, "caption": caption, "browser_action": action})
            def stamp(value):
                return f"00:{value // 60:02d}:{value % 60:02d},000"
            subtitles.append(f"{idx+1}\n{stamp(elapsed)} --> {stamp(elapsed+seconds)}\n{title}\n{caption}\n")
            elapsed += seconds
        process.stdin.close()
        error = process.stderr.read().decode(errors="replace")
        if process.wait(timeout=60):
            raise RuntimeError("Demo video encode failed: " + error[-1000:])
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=10)
        evaluate("document.getElementById('video-caption')?.remove()")
    (output / "chapter6_demo_4m20s.srt").write_text("\n".join(subtitles), encoding="utf-8", newline="\n")
    report = {"kind": "timed_browser_frames_with_captions_not_live_API_recording", "duration_seconds": elapsed,
              "width": 1440, "height": 1080, "fps": 15, "audio_track": False, "scenes": records,
              "new_model_calls": 0, "video_sha256": file_sha(target)}
    save_json(output / "storyboard.json", report, immutable=True)
    return report
