# -*- coding: utf-8 -*-
"""
嘉庚智慧馆员 · Agent 服务（SSE 流式）
启动：python app.py  →  http://127.0.0.1:8000
"""
import json
import time
import secrets
from flask import Flask, request, Response, jsonify, send_from_directory

import library_engine as le

app = Flask(__name__, static_folder='.', static_url_path='')

# 首次启动预热数据
print('⏳ 预热数据引擎中……')
t0 = time.time()
for fn in (le.collection_stats, le.top_borrowed_books, le.gate_department_rank):
    try:
        fn()
    except FileNotFoundError as e:
        print('⚠️', e)
print(f'✅ 服务就绪（数据源：{le.BASE or "未找到，请配置 LIBRARY_DATA_DIR"}；DeepSeek：{"已接入" if le.llm_available() else "未接入(降级模式)"}），预热 {time.time()-t0:.1f}s')


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json(silent=True) or {}
    q = (data.get('message') or '').strip()
    sid = (data.get('session_id') or '').strip()
    if not q:
        return jsonify({'ok': False, 'msg': '请输入问题'})
    if not sid or len(sid) > 64:
        sid = secrets.token_hex(8)
    t0 = time.time()

    def gen():
        yield 'data: ' + json.dumps({'type': 'hello', 'session_id': sid}, ensure_ascii=False) + '\n\n'
        ok, events = le.agent_handle(sid, q)
        if not ok:
            try:
                answer = le._fallback_answer(q)
            except Exception as e:
                answer = f'⚠️ 服务暂时不可用：{e}'
            yield 'data: ' + json.dumps({'type': 'delta', 'text': answer,
                                         'mode': 'fallback'}, ensure_ascii=False) + '\n\n'
        else:
            for typ, txt in events:
                yield 'data: ' + json.dumps({'type': typ, 'text': txt,
                                             'mode': 'agent'}, ensure_ascii=False) + '\n\n'
        yield 'data: ' + json.dumps({'type': 'done', 'cost_ms': int((time.time() - t0) * 1000)},
                                    ensure_ascii=False) + '\n\n'

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                             'Connection': 'keep-alive'})


@app.route('/api/reset', methods=['POST'])
def reset():
    data = request.get_json(silent=True) or {}
    sid = (data.get('session_id') or '').strip()
    if sid:
        le.reset_session(sid)
    return jsonify({'ok': True})


@app.route('/api/report')
def report():
    rid = request.args.get('reader_id', '').strip()
    if not rid:
        return jsonify({'ok': False, 'msg': '缺少reader_id参数'})
    try:
        return jsonify({'ok': True, 'data': le.reader_reading_report(rid)})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/stats')
def stats():
    try:
        return jsonify({'ok': True, 'data': le.dashboard_stats()})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/help')
def help():
    return jsonify({'ok': True, 'answer': le.HELP_TEXT})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8000, threaded=True)
