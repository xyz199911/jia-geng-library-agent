# -*- coding: utf-8 -*-
"""
嘉庚智慧馆员 · Agent 引擎 v3
架构（借鉴开源 library-agent 项目的 agent 模式）：
  意图预过滤 → LLM 工具调用循环（DeepSeek function calling）→ 真实数据工具 → 多轮上下文
  降级链：Agent 失败 → 规则引擎（离线保底）
数据：馆藏45.3万 / 借阅3.7万 / 门禁 / 座位 / 研讨室 / 备考联盟 / 读者 / 学者库4万篇
"""
import os
import json
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
import pandas as pd

# ============ 路径探测 ============
BASE = None
for _p in [os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'extracted'),
           os.path.join(os.path.dirname(os.path.abspath(__file__)), 'extracted'),
           os.path.join(os.getcwd(), 'extracted'),
           os.path.join(os.getcwd(), 'data')]:
    if os.path.isdir(_p):
        BASE = _p
        break
if os.environ.get('LIBRARY_DATA_DIR') and os.path.isdir(os.environ['LIBRARY_DATA_DIR']):
    BASE = os.environ['LIBRARY_DATA_DIR']

def _missing(name):
    return '[数据缺失] ' + name + ' 未找到（请在 demo 同级目录放 extracted/ 数据文件夹）'

# ============ 数据加载（带缓存） ============
_cache = {}

def _load_books():
    if 'books' in _cache:
        return _cache['books']
    p = os.path.join(BASE, '借阅数据', '图书数据.csv') if BASE else ''
    if not (BASE and os.path.exists(p)):
        raise FileNotFoundError(_missing('馆藏目录图书数据.csv'))
    df = pd.read_csv(p, usecols=['TITLE', 'AUTHOR', 'PUBLISHER', 'YEAR', 'CALLNO', 'LANGUAGE', 'DOCTYPE'],
                     encoding='utf-8-sig', on_bad_lines='skip', dtype={'YEAR': str})
    _cache['books'] = df
    return df

def _load_borrow():
    if 'borrow' in _cache:
        return _cache['borrow']
    p = os.path.join(BASE, '..', '图书借阅数据.xlsx')
    if not os.path.exists(p):
        raise FileNotFoundError(_missing('图书借阅数据.xlsx'))
    df = pd.read_excel(p)
    _cache['borrow'] = df
    return df

def _load_gate_cnt():
    if 'gate_cnt' in _cache:
        return _cache['gate_cnt']
    df = pd.read_excel(os.path.join(BASE, '门禁入馆数据', '门禁入馆人次.xlsx'))
    _cache['gate_cnt'] = df
    return df

def _load_gate_flow():
    if 'gate_flow' in _cache:
        return _cache['gate_flow']
    df = pd.read_excel(os.path.join(BASE, '门禁刷卡数据', '门禁刷卡.xlsx'))
    df['Cdate'] = pd.to_datetime(df['Cdate'], errors='coerce')
    df['hour'] = df['Cdate'].dt.hour
    _cache['gate_flow'] = df
    return df

def _load_seat():
    if 'seat' in _cache:
        return _cache['seat']
    df = pd.read_csv(os.path.join(BASE, '座位系统数据', '座位预约.csv'), encoding='gb18030', on_bad_lines='skip')
    _cache['seat'] = df
    return df

def _load_room():
    if 'room' in _cache:
        return _cache['room']
    df = pd.read_excel(os.path.join(BASE, '研讨室数据', '研讨室预约.xlsx'), header=1)
    _cache['room'] = df
    return df

def _load_exam():
    if 'exam' in _cache:
        return _cache['exam']
    df = pd.read_excel(os.path.join(BASE, '备考联盟数据', '备考联盟.xlsx'))
    _cache['exam'] = df
    return df

def _load_reader():
    if 'reader' in _cache:
        return _cache['reader']
    df = pd.read_csv(os.path.join(BASE, '读者数据', '读者数据.csv'), encoding='gb18030', on_bad_lines='skip')
    _cache['reader'] = df
    return df

def _load_scholar():
    if 'scholar' in _cache:
        return _cache['scholar']
    df = pd.read_excel(os.path.join(BASE, '学者库数据', '学者库数据.xlsx'))
    _cache['scholar'] = df
    return df

# ============ 数据分析函数（供工具调用） ============
def top_borrowed_books(n=10):
    df = _load_borrow()
    top = df.groupby('题名').size().sort_values(ascending=False).head(n)
    return [{'title': str(k)[:50], 'count': int(v)} for k, v in top.items()]

def search_book(keyword, limit=8):
    df = _load_books()
    m = df[df['TITLE'].astype(str).str.contains(keyword, na=False, regex=False)].head(limit)
    out = []
    for _, r in m.iterrows():
        out.append({'title': str(r['TITLE'])[:60], 'author': str(r['AUTHOR'])[:30] if pd.notna(r['AUTHOR']) else '',
                    'publisher': str(r['PUBLISHER'])[:30] if pd.notna(r['PUBLISHER']) else '',
                    'year': str(r['YEAR'])[:8] if pd.notna(r['YEAR']) else '',
                    'callno': str(r['CALLNO'])[:15] if pd.notna(r['CALLNO']) else ''})
    return out

def borrow_stats_by_title(keyword):
    df = _load_borrow()
    m = df[df['题名'].astype(str).str.contains(keyword, na=False, regex=False)]
    if len(m) == 0:
        return None
    top_pub = m['出版社'].value_counts().head(1)
    return {'keyword': keyword, 'borrow_count': int(len(m)),
            'most_publisher': str(top_pub.index[0]) if len(top_pub) else '未知'}

def collection_stats():
    df = _load_books()
    lang = df['LANGUAGE'].value_counts().head(4).to_dict()
    dtype = df['DOCTYPE'].value_counts().head(4).to_dict()
    year_clean = pd.to_numeric(df['YEAR'], errors='coerce')
    return {'total': int(len(df)),
            'language': {str(k): int(v) for k, v in lang.items()},
            'doctype': {str(k): int(v) for k, v in dtype.items()},
            'year_min': int(year_clean.min()) if pd.notna(year_clean.min()) else None,
            'year_max': int(year_clean.max()) if pd.notna(year_clean.max()) else None}

def collection_category():
    df = _load_books()
    c = df['CALLNO'].astype(str).str[0]
    c = c[c.str.isalpha()].value_counts().head(12)
    return [{'cat': str(k), 'count': int(v)} for k, v in c.items()]

def publisher_top(n=10):
    df = _load_books()
    top = df['PUBLISHER'].value_counts().head(n)
    return [{'publisher': str(k)[:30], 'count': int(v)} for k, v in top.items()]

def gate_department_rank(n=10):
    df = _load_gate_cnt()
    g = df.groupby('DEPARTMENT')['CNT'].sum().sort_values(ascending=False).head(n)
    return [{'department': str(k), 'count': int(v)} for k, v in g.items()]

def gate_hour_dist():
    df = _load_gate_flow()
    h = df['hour'].dropna().astype(int).value_counts().sort_index()
    return [{'hour': int(k), 'count': int(v)} for k, v in h.items()]

def gate_gender_ratio():
    df = _load_gate_flow()
    g = df['Gender'].value_counts().to_dict()
    return {'male': int(g.get('M', 0)), 'female': int(g.get('F', 0)), 'total': int(sum(g.values()))}

def gate_peak_weeks():
    df = _load_gate_flow()
    d = df['Cdate'].dt.dayofweek.dropna().astype(int)
    w = d.value_counts().sort_index()
    names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    return [{'weekday': names[int(k)], 'count': int(v)} for k, v in w.items()]

def seat_hot_area():
    df = _load_seat()
    area = df.groupby(['楼层', '区域']).size().sort_values(ascending=False).head(8)
    return [{'floor': str(k[0]), 'area': str(k[1]), 'count': int(v)} for k, v in area.items()]

def seat_time_dist():
    df = _load_seat()
    t = df['时间段'].value_counts().head(10)
    return [{'slot': str(k), 'count': int(v)} for k, v in t.items()]

def room_hot():
    df = _load_room()
    t = df['研讨室'].value_counts().head(8)
    return [{'room': str(k), 'count': int(v)} for k, v in t.items()]

def exam_stats():
    df = _load_exam()
    t = df['备考类型'].value_counts()
    g = df['性别'].value_counts()
    return {'types': [{'type': str(k), 'count': int(v)} for k, v in t.items()],
            'gender': {'male': int(g.get('M', 0)), 'female': int(g.get('F', 0))}}

def reader_profile():
    df = _load_reader()
    t = df['TYPE'].value_counts().to_dict()
    d = df['DEPARTMENT'].value_counts().head(8)
    return {'type': {str(k): int(v) for k, v in t.items()},
            'department': [{'dept': str(k)[:20], 'count': int(v)} for k, v in d.items()]}

def scholar_stats():
    df = _load_scholar()
    top_j = df['期刊名称'].value_counts().head(5)
    return {'total_papers': int(len(df)),
            'journals': [{'journal': str(k)[:30], 'count': int(v)} for k, v in top_j.items()],
            'year_min': str(df['发表日期'].astype(str).min())[:4] if len(df) else None}

def dashboard_stats():
    """数据面板：馆藏/借阅/读者/门禁/座位/学者 KPI + 热门书TOP5 + 热点TOP5 + 零借阅TOP3"""
    if 'dash' in _cache:
        return _cache['dash']
    out = {}
    try:
        cs = collection_stats()
        out['collection'] = {'total': int(cs.get('total', 0)),
                             'langs': {str(k)[:5]: int(v) for k, v in list(cs.get('language', {}).items())[:3]},
                             'year_max': cs.get('year_max')}
    except Exception:
        out['collection'] = {}
    try:
        b = _load_borrow()
        out['borrow'] = {'total': int(len(b)), 'readers': int(b['证件号'].nunique()),
                         'books': int(b['题名'].nunique())}
    except Exception:
        out['borrow'] = {}
    try:
        out['gate'] = {'total': int(_load_gate_cnt()['CNT'].sum())}
    except Exception:
        out['gate'] = {}
    try:
        out['seat'] = {'total': int(_load_seat().get('total_rows', 0))}
    except Exception:
        out['seat'] = {}
    try:
        out['scholar'] = int(scholar_stats().get('total_papers', 0))
    except Exception:
        out['scholar'] = 0
    try:
        out['top_books'] = top_borrowed_books(5)
    except Exception:
        out['top_books'] = []
    try:
        ht = hot_topic_books()
        out['hot_topics'] = [{'name': t['name'], 'icon': t['icon']} for t in ht.get('hot_topics', [])[:5]]
    except Exception:
        out['hot_topics'] = []
    try:
        out['zero_books'] = [{'title': z['title'][:22], 'callno': z['callno']} for z in zero_borrow_good_books(3)]
    except Exception:
        out['zero_books'] = []
    _cache['dash'] = out
    return out


def search_scholars(topic, top=5):
    """按主题检索学者及其论文（学术地图雏形）"""
    df = _load_scholar()
    m = df[df['论文题名'].astype(str).str.contains(topic, na=False, regex=False)
           | df['论文分类'].astype(str).str.contains(topic, na=False, regex=False)]
    if len(m) == 0:
        return {'found': False, 'topic': topic}
    by_author = m.groupby('作者').size().sort_values(ascending=False).head(top)
    scholars = []
    for author, cnt in by_author.items():
        sub = m[m['作者'] == author]
        papers = sub.head(3)['论文题名'].astype(str).tolist()
        journals = sub['期刊名称'].value_counts().head(2).index.astype(str).tolist()
        scholars.append({'author': str(author), 'papers_count': int(cnt),
                         'sample_titles': [p[:45] for p in papers],
                         'journals': [j[:25] for j in journals]})
    return {'found': True, 'topic': topic, 'total_hits': int(len(m)), 'scholars': scholars}

def scholar_by_author(author, top=6):
    df = _load_scholar()
    m = df[df['作者'].astype(str).str.contains(author, na=False, regex=False)]
    if len(m) == 0:
        return {'found': False, 'author': author}
    papers = []
    for _, r in m.head(top).iterrows():
        papers.append({'title': str(r['论文题名'])[:60],
                       'journal': str(r['期刊名称'])[:30] if pd.notna(r['期刊名称']) else '',
                       'year': str(r['发表日期'])[:10] if pd.notna(r['发表日期']) else ''})
    return {'found': True, 'author': author, 'total_papers': int(len(m)), 'papers': papers}



# ============ 实时状态与空间导航（P0：现在能自习吗 / 书在哪） ============
# 官方开馆时间（library.xujc.com 入馆须知，2026-07 更新）
LIB_OPEN_HOURS = '周一至周日 07:00-23:00（借还 08:00-22:30；周三12:00-17:00系统维护，前台暂停，自助区照常）'
# 中图法大类 → 楼层（嘉庚官方布局；2026年8月馆藏布局优化后以馆内新导览为准）
CATEGORY_FLOOR = {
    'A': ('2楼', '马克思主义、列宁主义、毛泽东思想、邓小平理论'), 'B': ('2楼', '哲学、宗教'),
    'C': ('2楼', '社会科学总论'), 'D': ('2楼', '政治、法律'), 'E': ('2楼', '军事'), 'F': ('2楼', '经济'),
    'G': ('3楼', '文化、科学、教育、体育'), 'H': ('3楼', '语言、文字'), 'I': ('3楼', '文学'),
    'K': ('3楼', '历史、地理'),
    'J': ('4楼', '艺术'), 'N': ('4楼', '自然科学总论'), 'O': ('4楼', '数理科学和化学'),
    'P': ('4楼', '天文学、地球科学'), 'Q': ('4楼', '生物科学'), 'R': ('4楼', '医药、卫生'),
    'S': ('4楼', '农业科学'), 'T': ('4楼', '工业技术'), 'U': ('4楼', '交通运输'),
    'V': ('4楼', '航空、航天'), 'X': ('4楼', '环境科学、安全科学'), 'Z': ('4楼', '综合性图书'),
}


def _load_gate_cards():
    """门禁刷卡按小时聚合（历史拥挤画像）"""
    if 'gate_cards' in _cache:
        return _cache['gate_cards']
    import glob as _glob
    cands = _glob.glob(os.path.join(BASE, '门禁刷卡数据', '*'))
    f = next((c for c in cands if c.lower().endswith(('.xlsx', '.xls'))), None)
    if f is None:
        raise FileNotFoundError('门禁刷卡数据缺失')
    df = pd.read_excel(f, usecols=['Cdate'])
    df['Cdate'] = pd.to_datetime(df['Cdate'], errors='coerce')
    hour = df['Cdate'].dt.hour.dropna()
    counts = hour.value_counts().sort_index()
    total = float(counts.sum())
    pct = (counts / total * 100).round(1).to_dict()
    _cache['gate_cards'] = {'total': int(total), 'hour_pct': pct,
                            'hour_count': counts.astype(int).to_dict()}
    return _cache['gate_cards']


def _load_seat():
    """座位预约聚合（时段/楼层热门）"""
    if 'seat' in _cache:
        return _cache['seat']
    import glob as _glob
    cands = _glob.glob(os.path.join(BASE, '座位系统数据', '*'))
    f = next((c for c in cands if c.lower().endswith('.csv')), None)
    if f is None:
        raise FileNotFoundError('座位系统数据缺失')
    df = pd.read_csv(f, encoding='gbk', on_bad_lines='skip')
    _cache['seat'] = {'segments': df['时间段'].value_counts().to_dict(),
                      'floors': {str(k): int(v) for k, v in df['楼层'].value_counts().to_dict().items()},
                      'total_rows': int(len(df))}
    return _cache['seat']


def live_library_status():
    """当前时间 + 开馆判断 + 当前时段拥挤预测"""
    now = datetime.now()
    hour = now.hour
    weekday_cn = '一二三四五六日'[now.weekday()]
    if 7 <= hour < 23:
        open_state = '开馆中'
        if 8 <= hour <= 22 and not (now.weekday() == 2 and 12 <= hour < 17):
            borrow_state = '可借还'
        else:
            borrow_state = '仅阅览（借还柜台 08:00-22:30；周三 12:00-17:00 系统维护前台暂停）'
        if 18 <= hour < 23:
            tip = '现在是晚高峰（历史18点前后入馆人次最多），2楼3楼座位最热，建议去4楼或提前占座'
        elif 12 <= hour < 18:
            tip = '午后入馆人次回升，想找安静座位建议 13:30 后或晚餐时段'
        else:
            tip = '上午相对宽松，9-10点为入馆高峰，越早越空'
    else:
        open_state = '已闭馆'
        borrow_state = '闭馆中'
        tip = '明早 07:00 开馆，建议开馆后 1 小时内到馆，座位最充足'
    gate = _load_gate_cards()
    pct = float(gate['hour_pct'].get(hour, 0))
    if pct >= 8:
        crowd = '高峰期（历史同点入馆占比最高档）'
    elif pct >= 4:
        crowd = '较忙'
    elif pct >= 1.5:
        crowd = '正常'
    else:
        crowd = '空闲'
    seat = _load_seat()
    return {
        'now': now.strftime('%Y-%m-%d %H:%M'), 'weekday': '周' + weekday_cn,
        'open_state': open_state, 'borrow_state': borrow_state,
        'open_hours': LIB_OPEN_HOURS, 'crowd_now': crowd,
        'hour_share_pct': pct, 'crowd_tip': tip,
        'seat_hot_segments': sorted(seat['segments'].items(), key=lambda x: -x[1])[:3],
        'seat_hot_floors': sorted(seat['floors'].items(), key=lambda x: -x[1]),
    }


def book_location(title, top_n=5):
    """按书名/主题查馆藏位置（索书号+中图法大类+楼层）"""
    books = _load_books()
    m = books[books['TITLE'].astype(str).str.contains(title, na=False, regex=False)]
    if len(m) == 0:
        return []
    out = []
    for _, r in m.head(top_n).iterrows():
        callno = str(r['CALLNO'])[:15] if pd.notna(r['CALLNO']) else ''
        cat = callno[:1].upper() if callno else ''
        floor, desc = CATEGORY_FLOOR.get(cat, ('（以馆内新导览为准）', '中图法 ' + cat))
        out.append({'title': str(r['TITLE'])[:55],
                    'author': str(r['AUTHOR'])[:25] if pd.notna(r['AUTHOR']) else '',
                    'callno': callno, 'floor': floor, 'category': desc,
                    'publisher': str(r['PUBLISHER'])[:22] if pd.notna(r['PUBLISHER']) else '',
                    'year': str(r['YEAR'])[:4] if pd.notna(r['YEAR']) else ''})
    return out


def _t_live_status(args):
    try:
        return live_library_status()
    except FileNotFoundError as e:
        return {'error': str(e), 'note': '可用官方开馆时间规则回答'}


def _t_book_location(args):
    title = str(args.get('title', '')).strip()
    top = min(int(args.get('top', 5)), 8)
    if not title:
        return {'error': '缺少书名关键词'}
    try:
        items = book_location(title, top)
        return {'found': len(items) > 0, 'query': title, 'items': items}
    except FileNotFoundError as e:
        return {'error': str(e)}


# ============ 推荐引擎（王晓东算法：过去 + 将来） ============
# 借鉴王晓东教授点评理念：抖音靠精准推荐算法让人放不下，
# 图书推荐既要考虑过去（协同过滤：相似读者的借阅）也要考虑将来（零借阅好书的探索推荐）。

def _build_rec_data():
    """构建推荐数据缓存：读者-图书矩阵、图书热度、零借阅书目"""
    if 'rec' in _cache:
        return _cache['rec']
    df = _load_borrow()[['证件号', '题名']].copy()
    df['证件号'] = df['证件号'].astype(str).str.strip()
    df['题名'] = df['题名'].astype(str).str.strip()
    patron_books = df.groupby('证件号')['题名'].apply(set).to_dict()
    book_pop = df['题名'].value_counts().to_dict()
    books = _load_books()
    coll_titles = set(books['TITLE'].astype(str).str.strip())
    zero = coll_titles - set(df['题名'])
    _cache['rec'] = {'patron_books': patron_books, 'book_pop': book_pop, 'zero': zero}
    return _cache['rec']


def cf_recommend(patron_id, top_n=8):
    """协同过滤（过去）：找相似读者 → 推荐他们借过而该读者没借的书"""
    d = _build_rec_data()
    my = d['patron_books'].get(patron_id)
    if not my:
        return None
    sims = []
    for other, ob in d['patron_books'].items():
        if other == patron_id:
            continue
        inter = len(my & ob)
        if inter == 0:
            continue
        sims.append((other, inter / len(my | ob)))  # Jaccard 相似度
    sims.sort(key=lambda x: -x[1])
    sims = sims[:20]
    if not sims:
        return []
    score = {}
    for other, jac in sims:
        for t in d['patron_books'][other] - my:
            pop = d['book_pop'].get(t, 0)
            score[t] = score.get(t, 0) + jac * (1 + pop ** 0.4)  # 相似度加权 + 热度弱加成
    ranked = sorted(score.items(), key=lambda x: -x[1])[:top_n]
    return [{'title': t, 'borrow_count': int(d['book_pop'].get(t, 0)), 'score': round(s, 3)}
            for t, s in ranked]


def zero_borrow_good_books(top_n=8):
    """零借阅好书挖掘（将来）：出版社声望 + 类目热度 + 年份 + 语种 综合质量分"""
    if 'zero_ranked' in _cache:
        return _cache['zero_ranked'][:top_n]
    books = _load_books()
    d = _build_rec_data()
    m = books[books['TITLE'].astype(str).str.strip().isin(d['zero'])]
    m = m[m['DOCTYPE'].astype(str).str.contains('图书')]
    borrow = _load_borrow()
    cat_pop = borrow['索书号'].astype(str).str[0]
    cat_pop = cat_pop[cat_pop.str.isalpha()].value_counts().to_dict()
    pub_count = m['PUBLISHER'].value_counts()
    pub_rank = {p: i for i, p in enumerate(pub_count.index[:60])}
    yr = pd.to_numeric(m['YEAR'], errors='coerce')

    def _cat_hot(c):
        return min(1.0, cat_pop.get(c, 0) / 3000)

    def _pub_s(p):
        return max(0.0, 1 - pub_rank.get(p, 99) / 60)

    def _yr_s(y):
        if pd.isna(y):
            return 0.1
        y = int(y)
        return 1.0 if 2012 <= y <= 2023 else (0.4 if y >= 2000 else 0.1)

    m = m.assign(
        cat_hot=m['CALLNO'].astype(str).str[0].map(_cat_hot),
        pub_s=m['PUBLISHER'].map(_pub_s),
        yr_s=yr.map(_yr_s),
        lang_s=m['LANGUAGE'].eq('中文').map({True: 0.15, False: 0.0}),
    )
    m['_q'] = m['pub_s'] * 0.35 + m['cat_hot'] * 0.25 + m['yr_s'] * 0.25 + m['lang_s']
    rows = m.nlargest(80, '_q')
    ranked = [{'title': str(r['TITLE'])[:60], 'author': str(r['AUTHOR'])[:30] if pd.notna(r['AUTHOR']) else '',
               'publisher': str(r['PUBLISHER'])[:25] if pd.notna(r['PUBLISHER']) else '',
               'year': str(r['YEAR'])[:4] if pd.notna(r['YEAR']) else '',
               'callno': str(r['CALLNO'])[:15] if pd.notna(r['CALLNO']) else '',
               'quality': round(float(r['_q']), 3)}
              for _, r in rows.iterrows()]
    _cache['zero_ranked'] = ranked
    return ranked[:top_n]


def reader_borrow_history(patron_id, top_n=10):
    """查询读者借阅历史"""
    df = _load_borrow()
    m = df[df['证件号'].astype(str).str.strip() == patron_id]
    if len(m) == 0:
        return None
    g = m.groupby('题名').size().sort_values(ascending=False).head(top_n)
    return {'patron': patron_id, 'total_borrows': int(len(m)),
            'distinct_books': int(m['题名'].nunique()),
            'books': [{'title': str(t)[:50], 'count': int(c)} for t, c in g.items()]}


def _tag_for(cat):
    """中图法大类 → 兴趣标签"""
    c = str(cat).upper()
    if c == 'A':
        return '思想理论'
    if c == 'B':
        return '哲学思辨'
    if c in 'CDEF':
        return '经管社科'
    if c == 'G':
        return '教育学习'
    if c == 'H':
        return '语言达人'
    if c == 'I':
        return '文学青年'
    if c == 'J':
        return '文艺范儿'
    if c == 'K':
        return '历史迷'
    if c in 'NOPQ':
        return '理科脑'
    if c == 'R':
        return '医学关注'
    if c == 'S':
        return '农科探索'
    if c in 'TUVX':
        return '工科技术党'
    if c == 'Z':
        return '杂学家'
    return '多元阅读'


def reader_reading_report(patron_id, top_n=6):
    """生成读者「年度阅读画像报告」：借阅量 + 类目分布 + 出版社/作者偏好 + 画像标签 + 推荐（过去+将来）"""
    df = _load_borrow()
    m = df[df['证件号'].astype(str).str.strip() == patron_id]
    if len(m) == 0:
        return {'found': False, 'patron': patron_id, 'reason': '查无此读者借阅记录'}
    cats = m['索书号'].astype(str).str[0]
    cats = cats[cats.str.isalpha()]
    cat_cnt = cats.value_counts().head(6)
    total_cat = int(len(cats)) or 1
    cat_items = []
    for k, v in cat_cnt.items():
        desc = CATEGORY_FLOOR.get(k, ('', '中图法 ' + k))[1]
        cat_items.append({'cat': str(k), 'name': desc, 'count': int(v),
                          'pct': int(round(v / total_cat * 100))})
    pubs = m['出版社'].dropna().value_counts().head(3)
    authors = m['责任者'].dropna().value_counts().head(3)
    books = m.groupby('题名').size().sort_values(ascending=False).head(top_n)
    tags = []
    seen = set()
    for k, _ in cat_cnt.items():
        t = _tag_for(k)
        if t not in seen:
            seen.add(t)
            tags.append(t)
        if len(tags) >= 3:
            break
    if not tags:
        tags = ['探索型读者']
    past = cf_recommend(patron_id, 3) or []
    future = zero_borrow_good_books(3)
    return {
        'found': True, 'patron': patron_id,
        'total_borrows': int(len(m)),
        'distinct_books': int(m['题名'].nunique()),
        'categories': cat_items,
        'top_publishers': [{'name': str(k)[:20], 'count': int(v)} for k, v in pubs.items()],
        'top_authors': [{'name': str(k)[:20], 'count': int(v)} for k, v in authors.items()],
        'top_books': [{'title': str(k)[:40], 'count': int(v)} for k, v in books.items()],
        'tags': tags,
        'recommend': {'personal': past[:3], 'zero': future[:3]},
    }


def _t_reading_report(args):
    patron = str(args.get('patron_id', '')).strip()
    if not patron:
        return {'error': '缺少读者ID'}
    try:
        return reader_reading_report(patron)
    except FileNotFoundError as e:
        return {'error': str(e)}


def topic_books(topic, top_n=6):
    """主题检索 + 借阅热度排序"""
    books = _load_books()
    m = books[books['TITLE'].astype(str).str.contains(topic, na=False, regex=False)]
    if len(m) == 0:
        return []
    borrow = _load_borrow()
    pop = borrow['题名'].astype(str).str.strip().value_counts().to_dict()
    m = m.copy()
    m['_pop'] = m['TITLE'].astype(str).str.strip().map(lambda t: pop.get(t, 0))
    rows = m.sort_values('_pop', ascending=False).head(top_n)
    return [{'title': str(r['TITLE'])[:50], 'author': str(r['AUTHOR'])[:25] if pd.notna(r['AUTHOR']) else '',
             'publisher': str(r['PUBLISHER'])[:25] if pd.notna(r['PUBLISHER']) else '',
             'year': str(r['YEAR'])[:4] if pd.notna(r['YEAR']) else '',
             'callno': str(r['CALLNO'])[:15] if pd.notna(r['CALLNO']) else '',
             'borrow_count': int(r['_pop'])}
            for _, r in rows.iterrows()]


# ============ 热点荐书（本周热点 x 个人兴趣 融合推荐） ============
# 设计理念：评委王晓东教授点评——精准推荐"既要考虑过去，也要考虑将来"。
# 协同过滤看"过去"（你借过什么），热点荐书看"将来"（本周全网在关心什么），
# 再用读者的中图法类目偏好把两者串起来，把热点相关的书优先推给感兴趣的人。
import os as _os

_HOT_SOURCE_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'hot_topics.json')


def _load_hot_topics():
    """加载热点库（demo/hot_topics.json，可按周更新），失败时回退空"""
    default = {'week': '', 'source': '', 'topics': []}
    try:
        if _os.path.exists(_HOT_SOURCE_FILE):
            with open(_HOT_SOURCE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('topics'), list):
                return data
    except Exception:
        pass
    return default


_HOT_DATA = _load_hot_topics()
_HOT_TOPICS = _HOT_DATA.get('topics', [])
_HOT_WEEK = _HOT_DATA.get('week', '')
_HOT_SOURCE = _HOT_DATA.get('source', '')

_CAT_DESC = {'A': '马列主义', 'B': '哲学心理', 'C': '社科总论', 'D': '法律政治', 'E': '军事', 'F': '经济管理',
             'G': '文化教育', 'H': '语言', 'I': '文学', 'J': '艺术', 'K': '历史地理', 'N': '自然科学总论',
             'O': '数理化', 'P': '天文地球', 'Q': '生物', 'R': '医药卫生', 'S': '农业', 'T': '工业技术',
             'U': '交通运输', 'V': '航空航天', 'X': '环境', 'Z': '综合'}


def _reader_cat_profile(patron_id):
    """读者借阅历史的中图法类目画像（top3 类目）"""
    try:
        df = _load_borrow()
        m = df[df['证件号'].astype(str).str.strip() == patron_id]
        if len(m) == 0:
            return {}
        cats = m['索书号'].astype(str).str[0]
        cats = cats[cats.str.isalpha()].value_counts()
        return {c: int(n) for c, n in cats.head(3).items()}
    except Exception:
        return {}


def _hot_candidates(name):
    """热点关键词 → 馆藏书目候选（缓存）"""
    if 'hot_books' not in _cache:
        _cache['hot_books'] = {}
    if name in _cache['hot_books']:
        return _cache['hot_books'][name]
    topic = next((t for t in _HOT_TOPICS if t['name'] == name), None)
    if not topic:
        return []
    books = _load_books()
    m = books[books['TITLE'].astype(str).str.contains('|'.join(topic['kws']), na=False, regex=True)]
    m = m[m['DOCTYPE'].astype(str).str.contains('图书')].head(80)
    out = []
    for _, r in m.iterrows():
        title = str(r['TITLE'])[:60]
        out.append({'title': title, 'author': str(r['AUTHOR'])[:30] if pd.notna(r['AUTHOR']) else '',
                    'publisher': str(r['PUBLISHER'])[:25] if pd.notna(r['PUBLISHER']) else '',
                    'year': str(r['YEAR'])[:4] if pd.notna(r['YEAR']) else '',
                    'callno': str(r['CALLNO'])[:15] if pd.notna(r['CALLNO']) else '',
                    'kw_hits': sum(1 for k in topic['kws'] if k in title)})
    _cache['hot_books'][name] = out
    return out


def hot_topic_books(reader_id='', topic='', top_n=6):
    """热点荐书：本周热点话题 x 读者兴趣（中图法类目）融合推荐。
    topic 为空时返回热点清单供用户挑选。"""
    topic = (topic or '').strip()
    if not topic:
        # 无参数：返回热点清单 + 前3个热点的真实馆藏书单预览（杜绝LLM编书名）
        previews = []
        for t in _HOT_TOPICS[:3]:
            c = _hot_candidates(t['name'])
            if not c:
                continue
            c = sorted(c, key=lambda x: -x['kw_hits'])[:3]
            previews.append({'topic': t['name'], 'icon': t['icon'], 'desc': t['desc'],
                             'books': [{'title': b['title'], 'author': b['author'], 'publisher': b['publisher'],
                                        'year': b['year'], 'callno': b['callno']} for b in c]})
        return {'found': True, 'week': _HOT_WEEK, 'source': _HOT_SOURCE,
                'hot_topics': [{'name': t['name'], 'icon': t['icon'], 'desc': t['desc']} for t in _HOT_TOPICS[:10]],
                'preview': previews,
                'tips': '回复时优先使用preview中的真实书单；也可告诉我想看的热点（如"AI大模型""亚运会"）或提供学号做兴趣加权'}
    hit = next((t for t in _HOT_TOPICS if t['name'] == topic or t['name'] in topic), None)
    if not hit:
        hit = next((t for t in _HOT_TOPICS if topic in t['name'] or t['name'][:3] in topic), None)
    if not hit:
        return {'found': False, 'reason': '暂未收录该热点，可试试：' + '、'.join(t['name'] for t in _HOT_TOPICS[:8]) + ' 等',
                'topics': [t['name'] for t in _HOT_TOPICS]}
    cands = _hot_candidates(hit['name'])
    if not cands:
        return {'found': False, 'reason': '该热点暂未匹配到馆藏图书，换一个热点试试', 'topic': hit['name']}
    # 读者兴趣（过去）：中图法类目画像
    cats = _reader_cat_profile(reader_id) if reader_id else {}
    cat_weight = {c: (3, 2, 1)[i] for i, c in enumerate(cats.keys())} if cats else {}
    borrow = _load_borrow()
    pop = borrow['题名'].astype(str).str.strip().value_counts().to_dict()
    for b in cands:
        c0 = str(b['callno'])[:1]
        b['_cat_bonus'] = cat_weight.get(c0, 0)
        b['_pop_s'] = pop.get(b['title'], 0)
        try:
            y = int(b['year']) if b['year'] else 0
            b['_yr_s'] = 0.5 if y >= 2023 else (0.3 if y >= 2018 else 0)
        except Exception:
            b['_yr_s'] = 0
        b['_score'] = b['kw_hits'] * 3 + b['_cat_bonus'] + min(b['_pop_s'] ** 0.3 * 0.1, 1) + b['_yr_s']
    cands.sort(key=lambda x: -x['_score'])
    items = [{'title': b['title'], 'author': b['author'], 'publisher': b['publisher'], 'year': b['year'],
              'callno': b['callno'], 'interest_match': bool(b['_cat_bonus']),
              'matched_cat': _CAT_DESC.get(str(b['callno'])[:1], '') if b['_cat_bonus'] else '',
              'borrow_count': int(b['_pop_s']), 'hot_kw_hits': int(b['kw_hits'])} for b in cands[:top_n]]
    return {'found': True, 'week': _HOT_WEEK, 'source': _HOT_SOURCE, 'hot_topic': hit['name'], 'desc': hit['desc'],
            'reader': reader_id, 'reader_cats': {c: _CAT_DESC.get(c, c) for c in cats.keys()},
            'interest_used': bool(cats), 'items': items}


def _t_hot_topic(args):
    reader = str(args.get('reader_id', '')).strip()
    topic = str(args.get('topic', '')).strip()
    top = min(int(args.get('top', 6)), 10)
    return hot_topic_books(reader_id=reader, topic=topic, top_n=top)


# ============ 顶刊文献热点（Nature/Science/Cell 近期热点研究方向 -> 荐书） ============
_HOT_PAPER_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'hot_papers.json')


def _load_hot_papers():
    default = {'period': '', 'source': '', 'topics': []}
    try:
        if _os.path.exists(_HOT_PAPER_FILE):
            with open(_HOT_PAPER_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('topics'), list):
                return data
    except Exception:
        pass
    return default


_HOT_PAPER_DATA = _load_hot_papers()
_HOT_PAPER_TOPICS = _HOT_PAPER_DATA.get('topics', [])
_HOT_PAPER_PERIOD = _HOT_PAPER_DATA.get('period', '')
_HOT_PAPER_SOURCE = _HOT_PAPER_DATA.get('source', '')


def _paper_candidates(name):
    if 'paper_books' not in _cache:
        _cache['paper_books'] = {}
    if name in _cache['paper_books']:
        return _cache['paper_books'][name]
    topic = next((t for t in _HOT_PAPER_TOPICS if t['name'] == name), None)
    if not topic:
        return []
    books = _load_books()
    m = books[books['TITLE'].astype(str).str.contains('|'.join(topic['kws']), na=False, regex=True)]
    m = m[m['DOCTYPE'].astype(str).str.contains('图书')].head(60)
    out = []
    for _, r in m.iterrows():
        title = str(r['TITLE'])[:60]
        out.append({'title': title, 'author': str(r['AUTHOR'])[:30] if pd.notna(r['AUTHOR']) else '',
                    'publisher': str(r['PUBLISHER'])[:25] if pd.notna(r['PUBLISHER']) else '',
                    'year': str(r['YEAR'])[:4] if pd.notna(r['YEAR']) else '',
                    'callno': str(r['CALLNO'])[:15] if pd.notna(r['CALLNO']) else '',
                    'kw_hits': sum(1 for k in topic['kws'] if k in title)})
    _cache['paper_books'][name] = out
    return out


def journal_hot_books(topic='', top_n=6):
    topic = (topic or '').strip()
    if not topic:
        previews = []
        for t in _HOT_PAPER_TOPICS[:3]:
            c = _paper_candidates(t['name'])
            if not c:
                continue
            c = sorted(c, key=lambda x: -x['kw_hits'])[:3]
            previews.append({'topic': t['name'], 'icon': t['icon'], 'desc': t['desc'],
                             'books': [{'title': b['title'], 'author': b['author'], 'publisher': b['publisher'],
                                        'year': b['year'], 'callno': b['callno']} for b in c]})
        return {'found': True, 'period': _HOT_PAPER_PERIOD, 'source': _HOT_PAPER_SOURCE,
                'topics': [{'name': t['name'], 'icon': t['icon'], 'desc': t['desc']} for t in _HOT_PAPER_TOPICS[:10]],
                'preview': previews,
                'tips': '回复时优先使用preview中的真实书单；也可告诉我想看的顶刊方向（如"脑机接口""量子计算"）'}
    hit = next((t for t in _HOT_PAPER_TOPICS if t['name'] == topic or t['name'] in topic), None)
    if not hit:
        hit = next((t for t in _HOT_PAPER_TOPICS if topic in t['name'] or t['name'][:3] in topic), None)
    if not hit:
        return {'found': False, 'reason': '暂未收录该顶刊方向，可试试：' + '、'.join(t['name'] for t in _HOT_PAPER_TOPICS[:8]) + ' 等',
                'topics': [t['name'] for t in _HOT_PAPER_TOPICS]}
    cands = _paper_candidates(hit['name'])
    if not cands:
        return {'found': False, 'reason': '该方向暂未匹配到馆藏图书，换一个试试', 'topic': hit['name']}
    cands.sort(key=lambda x: (-x['kw_hits'], -int(x['year']) if x['year'].isdigit() else 0))
    items = [{'title': b['title'], 'author': b['author'], 'publisher': b['publisher'], 'year': b['year'],
              'callno': b['callno'], 'hot_kw_hits': int(b['kw_hits'])} for b in cands[:top_n]]
    return {'found': True, 'period': _HOT_PAPER_PERIOD, 'source': _HOT_PAPER_SOURCE,
            'paper_topic': hit['name'], 'desc': hit['desc'], 'items': items}


def _t_journal_hot(args):
    topic = str(args.get('topic', '')).strip()
    top = min(int(args.get('top', 6)), 10)
    return journal_hot_books(topic=topic, top_n=top)


def _t_recommend(args):
    mode = str(args.get('mode', 'hybrid'))
    patron = str(args.get('patron_id', '')).strip()
    topic = str(args.get('topic', '')).strip()
    top = min(int(args.get('top', 6)), 10)
    try:
        if mode == 'personal':
            items = cf_recommend(patron, top)
            if items is None:
                return {'found': False, 'reason': '读者ID不存在或暂无借阅记录，可提供学号/证件号（如SSCCLL21080）', 'patron': patron}
            return {'found': True, 'algorithm': '协同过滤（基于相似读者的借阅历史）', 'patron': patron, 'items': items}
        if mode == 'zero':
            return {'found': True, 'algorithm': '零借阅好书挖掘（出版社声望+类目热度+年份+语种）', 'items': zero_borrow_good_books(top)}
        if mode == 'topic':
            items = topic_books(topic, top)
            return {'found': len(items) > 0, 'algorithm': '主题检索+借阅热度排序', 'topic': topic, 'items': items}
        # hybrid 默认：王晓东算法 —— 过去(协同过滤) + 将来(零借阅好书)
        past = cf_recommend(patron, top) if patron else []
        if past is None:
            past = []
        future = zero_borrow_good_books(top)
        return {'found': True,
                'algorithm': '混合推荐（王晓东算法）：协同过滤(过去) × 零借阅好书(将来)',
                'patron': patron or '未提供(仅输出将来部分)',
                'past': past, 'future': future}
    except FileNotFoundError as e:
        return {'error': str(e)}


def _t_borrow_history(args):
    patron = str(args.get('patron_id', '')).strip()
    if not patron:
        return {'error': '缺少读者ID'}
    try:
        r = reader_borrow_history(patron)
        if r is None:
            return {'found': False, 'patron': patron, 'reason': '查无此读者借阅记录'}
        return {'found': True, **r}
    except FileNotFoundError as e:
        return {'error': str(e)}


# ============ DeepSeek LLM 客户端 ============
def _load_config():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {}

CONFIG = _load_config()
DEEPSEEK_KEY = CONFIG.get('deepseek_key') or os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_MODEL = CONFIG.get('model', 'deepseek-chat')
DEEPSEEK_URL = CONFIG.get('api_url', 'https://api.deepseek.com/chat/completions')

def llm_call(messages, tools=None, temperature=0.5, max_tokens=1300, timeout=60):
    """调用 DeepSeek（OpenAI 兼容）。成功返回 choices[0] 的 message dict，失败返回 None。"""
    if not DEEPSEEK_KEY:
        return None
    payload = {'model': DEEPSEEK_MODEL, 'messages': messages, 'temperature': temperature,
               'max_tokens': max_tokens, 'stream': False}
    if tools:
        payload['tools'] = tools
        payload['tool_choice'] = 'auto'
    req = urllib.request.Request(
        DEEPSEEK_URL, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + DEEPSEEK_KEY})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            if data.get('error'):
                print('[LLM error]', data['error'])
                return None
            msg = data['choices'][0].get('message')
            if not msg:
                return None
            return msg
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or e.code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            body = e.read().decode('utf-8', errors='ignore')[:200]
            print('[LLM HTTPError]', e.code, body)
            return None
        except Exception as e:
            last_err = e
            print('[LLM error]', e)
            time.sleep(1.5 * (attempt + 1))
    print('[LLM failed after retries]', last_err)
    return None

def llm_available():
    return bool(DEEPSEEK_KEY)

# ============ 意图预过滤（借鉴开源 filter 模式，节省 token） ============
_LIB_KW = ['书', '借', '还', '续', '预约', '排队', '罚款', '馆藏', '藏书', '读者', '统计',
           '多少本', '有没有', '推荐', '考研', '考公', '考编', '座位', '研讨室', '门禁', '入馆',
           '进馆', '学院', '高峰', '几点', '学者', '论文', '期刊', '老师', '研究', '学报',
           '学号', '证件号', '借阅记录', '阅读', '教材', '课程', '复习',
           '热点', '时事', '本周', '最近大家', '热门话题',
           '阅读报告', '阅读画像', '画像', '年度报告', '年度阅读',
           'nature', 'science', 'cell', '顶刊', '文献', '期刊热点']
_UNRELATED = ['天气', '股票', '基金', '比特币', '投资', '理财', '政治', '新闻', '美食', '菜谱',
              '做饭', '电影', '电视剧', '游戏', '化妆', '穿搭', '减肥', '健身', '写诗', '作诗',
              '写小说', '写代码', '翻译', '租房', '买房', '买车', '彩票', '八卦', '追星',
              '恋爱', '婚姻', '星座', '算命', '笑话', '彩票', '体育比分',
              '演艺圈', '网红八卦']
_GREET = ['你好', '您好', '嗨', '哈喽', 'hello', 'hi', '在吗', '谢谢', '谢了', '辛苦了',
          '再见', '拜拜', '早安', '晚安']

WELCOME = ('你好！我是「嘉庚智慧馆员」，可以帮你查馆藏、找书、看借阅排行、查门禁和座位数据、'
           '了解备考联盟，还能检索嘉庚学者的学术成果。想问点什么？')
OUT_OF_SCOPE = '抱歉，我只负责厦门大学嘉庚学院图书馆相关的事务（查书、荐书、馆藏、门禁、座位、学者论文等）。其他问题可以咨询相应渠道哦。'

def prefilter(msg):
    """返回 (拦截与否, 回复文本)。拦截 = 不调用 LLM。"""
    m = msg.strip().lower()
    if not m:
        return True, WELCOME
    if '《' in msg:
        return False, ''
    for k in _LIB_KW:
        if k in msg:
            return False, ''
    for k in _UNRELATED:
        if k in msg:
            return True, OUT_OF_SCOPE
    for k in _GREET:
        if k in m:
            return True, WELCOME
    return False, ''

# ============ Agent：工具注册表 ============
def _fmt_books(items):
    return {'found': len(items) > 0, 'count': len(items), 'books': items}

def _t_search_books(args):
    kw = str(args.get('keyword', '')).strip()
    limit = min(int(args.get('limit', 6)), 10)
    if not kw:
        return {'error': '缺少检索关键词'}
    try:
        items = search_book(kw, limit)
        return _fmt_books(items)
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_borrow_stats(args):
    title = str(args.get('title', '')).strip()
    if not title:
        return {'error': '缺少书名'}
    r = borrow_stats_by_title(title)
    if r is None:
        return {'found': False, 'title': title}
    return {'found': True, 'title': title, 'borrow_count': r['borrow_count'],
            'most_publisher': r['most_publisher']}

def _t_collection(args):
    try:
        return collection_stats()
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_top_borrowed(args):
    n = min(int(args.get('limit', 10)), 20)
    try:
        return {'items': top_borrowed_books(n)}
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_gate(args):
    aspect = str(args.get('aspect', 'department'))
    try:
        if aspect == 'hour':
            return {'aspect': 'hour', 'items': gate_hour_dist()}
        if aspect == 'gender':
            return {'aspect': 'gender', **gate_gender_ratio()}
        if aspect == 'weekday':
            return {'aspect': 'weekday', 'items': gate_peak_weeks()}
        return {'aspect': 'department', 'items': gate_department_rank(10)}
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_seat(args):
    aspect = str(args.get('aspect', 'area'))
    try:
        if aspect == 'time':
            return {'aspect': 'time', 'items': seat_time_dist()}
        return {'aspect': 'area', 'items': seat_hot_area()}
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_room(args):
    try:
        return {'items': room_hot()}
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_exam(args):
    try:
        return exam_stats()
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_reader(args):
    try:
        return reader_profile()
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_scholar_stats(args):
    try:
        return scholar_stats()
    except FileNotFoundError as e:
        return {'error': str(e)}

# 中文研究主题 → 英文关键词扩展（学者库论文以英文为主）
_TOPIC_EXPAND = {
    '人工智能': ['artificial intelligence', 'intelligence', 'ai'],
    '机器学习': ['machine learning', 'learning'],
    '深度学习': ['deep learning', 'neural'],
    '神经网络': ['neural network', 'neural'],
    '大数据': ['big data', 'data mining', 'data'],
    '数据挖掘': ['data mining', 'data'],
    '计算机': ['computer', 'computing', 'software'],
    '软件': ['software', 'system'],
    '网络安全': ['security', 'cyber', 'network security'],
    '图像': ['image', 'vision', 'imaging'],
    '视觉': ['vision', 'image'],
    '机器人': ['robot', 'robotics'],
    '智能': ['smart', 'intelligence', 'intelligent'],
    '金融': ['finance', 'financial', 'bank'],
    '经济': ['econom', 'economic'],
    '水处理': ['water treatment', 'water'],
    '环境': ['environment', 'environmental', 'water', 'marine'],
    '海洋': ['marine', 'ocean', 'estuar'],
    '能源': ['energy', 'power', 'solar'],
    '光伏': ['solar', 'photovoltaic', 'pv'],
    '材料': ['material', 'nanoparticle', 'polymer'],
    '医学': ['medical', 'medicine', 'clinic'],
    '医疗': ['medical', 'health'],
    '癌症': ['cancer', 'tumor', 'tumour'],
    '化学': ['chem', 'chemical'],
    '物理': ['physic'],
    '数学': ['math', 'mathematical', 'graph theory'],
    '控制': ['control', 'automat'],
    '通信': ['communication', 'signal', 'wireless'],
    '电子': ['electronic', 'circuit', 'semiconductor'],
    '生物': ['bio', 'biology', 'genom'],
    '农业': ['agricult', 'crop', 'soil'],
    '教育': ['education', 'teaching', 'learning'],
    '管理': ['management', 'operation'],
    '物流': ['logistic', 'supply chain'],
}

def _expand_topics(topic):
    """返回候选检索词列表：中文映射 + 原词 + 去空"""
    cands = []
    if topic in _TOPIC_EXPAND:
        cands += _TOPIC_EXPAND[topic]
    cands.append(topic)
    out = []
    for c in cands:
        c = c.strip()
        if c and c not in out:
            out.append(c)
    return out

def _t_scholar_topic(args):
    topic = str(args.get('topic', '')).strip()
    if not topic:
        return {'error': '缺少研究主题'}
    top = int(args.get('top', 5))
    try:
        # 候选词按精确度排序：取第一个命中数足够（>=8）的词作为主结果
        cands = _expand_topics(topic)
        best = None
        for cand in cands:
            r = search_scholars(cand, top)
            if r['found']:
                r['matched_keyword'] = cand
                if r['total_hits'] >= 2:
                    return r
                if best is None or r['total_hits'] > best['total_hits']:
                    best = r
        return best if best else {'found': False, 'topic': topic}
    except FileNotFoundError as e:
        return {'error': str(e)}


def _t_scholar_author(args):
    author = str(args.get('author', '')).strip()
    if not author:
        return {'error': '缺少学者姓名'}
    try:
        return scholar_by_author(author, int(args.get('top', 6)))
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_publisher(args):
    try:
        return {'items': publisher_top(10)}
    except FileNotFoundError as e:
        return {'error': str(e)}

def _t_category(args):
    try:
        return {'items': collection_category()}
    except FileNotFoundError as e:
        return {'error': str(e)}

# 工具定义（OpenAI/DeepSeek function calling 格式）
TOOLS = [
    {'type': 'function', 'function': {'name': 'search_books', 'description': '按书名/主题关键词检索馆藏书目，返回书目列表（含作者、出版社、年份）。用户想找书、查某本书是否存在、或要求推荐某主题的书时使用。',
        'parameters': {'type': 'object', 'properties': {'keyword': {'type': 'string', 'description': '书名或主题关键词，如：三体、经济学、Python'}, 'limit': {'type': 'integer', 'description': '返回条数，默认6'}}, 'required': ['keyword']}}},
    {'type': 'function', 'function': {'name': 'get_book_borrow_stats', 'description': '查询某本书的借阅次数和最常借出的出版社（基于真实借阅流水）。用户问《某书》被借了几次、借阅热度时使用。',
        'parameters': {'type': 'object', 'properties': {'title': {'type': 'string', 'description': '书名关键词'}}, 'required': ['title']}}},
    {'type': 'function', 'function': {'name': 'get_collection_stats', 'description': '查询全馆馆藏总量、语种构成、文献类型、入藏年份范围。用户问图书馆有多少书、藏书规模时使用。无参数。',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'get_top_borrowed', 'description': '查询借阅次数最多的图书排行（借阅榜TOP）。用户问最受欢迎的图书、借得最多的书时使用。',
        'parameters': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'description': '条数，默认10'}}}}},
    {'type': 'function', 'function': {'name': 'get_publisher_rank', 'description': '查询馆藏量最多的出版社排行。用户问出版社、出版排行时使用。无参数。',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'get_category_stats', 'description': '查询馆藏中图法分类分布（索书号首字母统计）。用户问图书分类、哪一类书多时使用。无参数。',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'get_gate_stats', 'description': '查询门禁入馆数据：学院入馆人次排名(department)、全天入馆高峰时段(hour)、入馆性别比例(gender)、周几入馆最多(weekday)。用户问哪个学院入馆最多、几点入馆人最多、男女比例、周几人多时使用。',
        'parameters': {'type': 'object', 'properties': {'aspect': {'type': 'string', 'enum': ['department', 'hour', 'gender', 'weekday'], 'description': '查询维度，默认department'}}}}},
    {'type': 'function', 'function': {'name': 'get_seat_stats', 'description': '查询座位预约数据：热门区域(area)或热门时段(time)。用户问座位、自习、区域、楼层时使用。',
        'parameters': {'type': 'object', 'properties': {'aspect': {'type': 'string', 'enum': ['area', 'time'], 'description': '默认area'}}}}},
    {'type': 'function', 'function': {'name': 'get_room_stats', 'description': '查询研讨室使用热度排行。用户问研讨室时使用。无参数。',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'get_exam_stats', 'description': '查询备考联盟预约统计（考研/考公/出国等类型分布与性别比例）。用户问考研人数、备考联盟时使用。无参数。',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'get_reader_profile', 'description': '查询读者构成（读者类型分布、院系分布）。用户问读者有多少、本科生构成时使用。无参数。',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'get_scholar_stats', 'description': '查询学者库总览：收录论文总数、高产期刊。用户问学者、论文总量、期刊时使用。无参数。',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'search_scholars', 'description': '按研究主题检索厦门大学嘉庚学院学者的论文成果（学术地图）：返回有哪些老师研究该主题、各发表多少篇、代表作与主要期刊。用户问我们学校哪些老师研究某主题、某领域学者时使用。',
        'parameters': {'type': 'object', 'properties': {'topic': {'type': 'string', 'description': '研究主题关键词，如：人工智能、水处理、金融'}, 'top': {'type': 'integer', 'description': '返回学者数，默认5'}}, 'required': ['topic']}}},
    {'type': 'function', 'function': {'name': 'get_scholar_papers', 'description': '查询某位学者的论文列表（题名、期刊、发表时间）。用户问某位老师/学者发了什么论文时使用。',
        'parameters': {'type': 'object', 'properties': {'author': {'type': 'string', 'description': '学者姓名或关键词'}, 'top': {'type': 'integer', 'description': '论文条数，默认6'}}, 'required': ['author']}}},
    {'type': 'function', 'function': {'name': 'recommend_books', 'description': '图书智能推荐，实现"过去+将来"混合推荐算法（借鉴精准推荐理念，评委王晓东教授点评方向）：personal=协同过滤，基于该读者与相似读者的借阅历史推荐（需patron_id证件号）；zero=挖掘零借阅好书，把出版社声望高、类目热门但还没人借过的书推荐出去；topic=按主题推荐并附借阅热度；hybrid=过去(协同过滤)+将来(零借阅好书)混合。用户说"推荐""有什么好书""适合我的书""零借阅""没人借过的好书""推荐几本书"时使用，可请用户提供证件号以获得个性化推荐。',
        'parameters': {'type': 'object', 'properties': {'mode': {'type': 'string', 'enum': ['personal', 'zero', 'topic', 'hybrid'], 'description': '推荐模式，默认hybrid'}, 'patron_id': {'type': 'string', 'description': '读者证件号（如SSCCLL21080），personal/hybrid模式需要'}, 'topic': {'type': 'string', 'description': '主题关键词，topic模式需要'}, 'top': {'type': 'integer', 'description': '条数，默认6'}}}}},
    {'type': 'function', 'function': {'name': 'get_reader_borrow_history', 'description': '查询某位读者的借阅历史（借过哪些书、借了几次）。用户说"我借了什么""我的借阅记录""查一下证件号/学号XX"时使用。',
        'parameters': {'type': 'object', 'properties': {'patron_id': {'type': 'string', 'description': '读者证件号/学号'}}, 'required': ['patron_id']}}},
    {'type': 'function', 'function': {'name': 'hot_topic_books', 'description': '热点荐书（本周热点x个人兴趣融合）：根据本周微博/抖音热搜与新闻热点推荐相关图书，可结合读者证件号做兴趣（中图法类目）加权。topic为空时返回本周热点清单。用户说"热点荐书""本周有什么热点""结合热点推荐书""最近大家在关心什么，推荐相关的书"时使用。',
        'parameters': {'type': 'object', 'properties': {'reader_id': {'type': 'string', 'description': '读者证件号/学号（可选，提供则结合其借阅兴趣）'}, 'topic': {'type': 'string', 'description': '热点名称，如：AI大模型与智能体、名古屋亚运会、新能源与储能（可选）'}, 'top': {'type': 'integer', 'description': '条数，默认6'}}}}},
    {'type': 'function', 'function': {'name': 'journal_hot_books', 'description': '顶刊文献热点荐书：Nature/Science/Cell 等国际顶刊近期热点研究方向（如脑机接口、AI for Science、量子计算、CRISPR基因编辑），映射到馆藏图书。topic为空时返回顶刊热点方向清单。用户说"Nature有什么热点""顶刊热点""文献热点""最近论文在关注什么方向"时使用。',
        'parameters': {'type': 'object', 'properties': {'topic': {'type': 'string', 'description': '顶刊热点方向名称，如：脑机接口、量子计算（可选）'}, 'top': {'type': 'integer', 'description': '条数，默认6'}}}}},
    {'type': 'function', 'function': {'name': 'get_reader_reading_report', 'description': '生成读者的「年度阅读画像报告」：统计借阅量、中图法类目分布、出版社与作者偏好、画像标签（如：工科技术党、文学青年），并附协同过滤（相似读者）+零借阅好书推荐。用户说“阅读报告”“阅读画像”“年度报告”“我的阅读报告”“帮我做个报告”“总结一下我的阅读”时使用。',
        'parameters': {'type': 'object', 'properties': {'patron_id': {'type': 'string', 'description': '读者证件号/学号'}}, 'required': ['patron_id']}}},
    {'type': 'function', 'function': {'name': 'get_live_library_status', 'description': '获取图书馆实时状态：当前时间、是否开馆、能否借还、当前时段拥挤度（基于门禁刷卡与座位预约历史数据预测）。用户问"现在能自习吗""图书馆开门吗""现在人多不多""几点去合适""现在闭馆了吗"时使用。',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {'name': 'get_book_location', 'description': '图书架位导航：按书名查询索书号、馆藏楼层与中图法分类区域。用户问"这本书在哪个书库""在哪一层""怎么找到这本书"时使用。',
        'parameters': {'type': 'object', 'properties': {'title': {'type': 'string', 'description': '书名或关键词'}, 'top': {'type': 'integer', 'description': '条数，默认5'}}, 'required': ['title']}}},
]

TOOL_HANDLERS = {
    'search_books': _t_search_books,
    'get_book_borrow_stats': _t_borrow_stats,
    'get_collection_stats': _t_collection,
    'get_top_borrowed': _t_top_borrowed,
    'get_publisher_rank': _t_publisher,
    'get_category_stats': _t_category,
    'get_gate_stats': _t_gate,
    'get_seat_stats': _t_seat,
    'get_room_stats': _t_room,
    'get_exam_stats': _t_exam,
    'get_reader_profile': _t_reader,
    'get_scholar_stats': _t_scholar_stats,
    'search_scholars': _t_scholar_topic,
    'get_scholar_papers': _t_scholar_author,
    'recommend_books': _t_recommend,
    'hot_topic_books': _t_hot_topic,
    'journal_hot_books': _t_journal_hot,
    'get_reader_borrow_history': _t_borrow_history,
    'get_reader_reading_report': _t_reading_report,
    'get_live_library_status': _t_live_status,
    'get_book_location': _t_book_location,
}

SYSTEM_PROMPT = (
    '你是厦门大学嘉庚学院图书馆的AI馆员「嘉庚智慧馆员」，负责服务全校师生。'
    '你拥有真实数据工具，可查询：馆藏45.3万册、借阅流水3.7万条、门禁入馆、座位预约、研讨室、'
    '备考联盟、读者信息、学者库约4万篇论文。\n'
    '工作方式：\n'
    '1. 用户问图书馆数据（藏书量、借阅排行、找书、荐书、门禁、座位、研讨室、备考、读者、学者论文）时，'
    '必须先调用对应工具获取真实数据，再基于工具返回结果组织回答。\n'
    '2. 推荐图书时优先调用 recommend_books（该工具实现了"过去+将来"混合推荐算法，'
    '灵感来自评委王晓东教授的点评：精准推荐既要考虑过去——协同过滤推荐相似读者在借的好书，'
    '也要考虑将来——把零借阅的好书（出版社声望高、类目热门但无人借阅）也推荐出去）。'
    '用户问"本周热点""结合热点推荐书"时调用 hot_topic_books：该工具内置本周微博/抖音热搜与新闻热点库（hot_topics.json，按周更新），'
    '算法为"热点关键词→馆藏匹配→读者中图法类目兴趣加权"。回答时要说明热点名称、本周时间范围与来源（微博/抖音热搜/新闻），'
    '并提示提供学号可获得兴趣加权推荐；热点书单同样要给出书名、作者、出版社、索书号与推荐理由。'
    '用户问"Nature/Science顶刊热点""文献热点"时调用 journal_hot_books：内置Nature/Science/Cell近两年高频研究方向（hot_papers.json），'
    '回答时说明来源期刊与时间范围，列出约10个研究方向，并从前3个方向挑真实馆藏图书做推荐。'
    '用户提供证件号（学号）时用 personal/hybrid 模式做个性化推荐；没提供时用 zero/topic 模式并提示可提供学号。'
    '学号规则：用户可能一次输入多个学号（空格/逗号分隔），应逐个调用 get_reader_borrow_history 查询并汇总对比；'
    '查询返回 found=false 时，如实说明该学号暂无借阅记录，然后改用热门图书或零借阅好书降级推荐，不要只回复"查无此人"结束对话。\n'
    '3. 写书评时：可先调用 get_book_borrow_stats 查该书借阅热度，用真实数据佐证；没读过的书不要编造内容细节。\n'
    '4. 多轮对话：要记住之前的对话内容，用户说"刚才那本""第一本""它"等指代时，结合上下文回答。'
    '6. 用户问"现在能自习吗""开门吗""几点去人多不多"时调用 get_live_library_status（实时时间+拥挤预测）；'
    '问"书在哪""哪个书库""哪一层"时调用 get_book_location（索书号+楼层导航）。\n'
    '5. 用户要求“阅读报告/阅读画像/年度报告”时调用 get_reader_reading_report（需学号），按返回的结构化数据组织一份像“网易云年度歌单”的阅读报告：'
    '标题用“你的2025-2026阅读画像”风格，依次讲借阅量、最爱类目（给占比）、常借出版社、画像标签，再附“相似读者也在读”和“零借阅宝藏”两组推荐；不要编造报告里没有的数字。'
    '6. 防幻觉：所有数字（藏书量、借阅次数、人次、篇数）必须以工具返回值为准，严禁编造；'
    '数据工具报错或未找到时如实说明，不猜测。\n'
    '回答要求（全面性优先）：\n'
    '- 使用简体中文，语气像一位懂行的学长学姐，自然、亲切、不油腻。\n'
    '- 推荐书单时给出完整信息：书名、作者、出版社、年份、索书号、借阅热度（如有）、推荐理由。\n'
    '- 回答统计类问题时，不只罗列数字，要给出解读与洞察（如"土木类工具书是借阅主力，反映工科特色"）。\n'
    '- 有榜单/列表时用表格或分条呈现，结构清晰；回答长度一般150-350字，内容充实但不啰嗦。\n'
    '- 可适当体现厦门大学嘉庚学院图书馆特色（如：闽南文化、嘉庚精神、学者库）。'
)

MAX_AGENT_ITER = 4
MAX_TOOL_RESULT = 1800

def _truncate(s, max_len=MAX_TOOL_RESULT):
    s = str(s)
    if len(s) <= max_len:
        return s
    return s[:max_len] + '\n…(结果过长已截断)'

def _execute_tool(name, args_str):
    if name not in TOOL_HANDLERS:
        return json.dumps({'error': '未知工具: ' + name}, ensure_ascii=False)
    try:
        args = json.loads(args_str) if args_str else {}
    except Exception:
        return json.dumps({'error': '工具参数解析失败'}, ensure_ascii=False)
    if not isinstance(args, dict):
        return json.dumps({'error': '工具参数必须是对象'}, ensure_ascii=False)
    try:
        result = TOOL_HANDLERS[name](args)
    except Exception as e:
        return json.dumps({'error': '工具执行失败: ' + str(e)}, ensure_ascii=False)
    return _truncate(json.dumps(result, ensure_ascii=False))

# ============ 会话记忆 ============
SESSIONS = {}
SESSION_TTL = timedelta(hours=2)
MAX_HISTORY = 20

def _get_session(sid):
    now = datetime.now()
    if sid not in SESSIONS or now - SESSIONS[sid]['ts'] > SESSION_TTL:
        SESSIONS[sid] = {'history': [], 'ts': now}
    s = SESSIONS[sid]
    s['ts'] = now
    return s

def _clean_sessions():
    now = datetime.now()
    expired = [k for k, v in SESSIONS.items() if now - v['ts'] > SESSION_TTL]
    for k in expired:
        SESSIONS.pop(k, None)

def reset_session(sid):
    SESSIONS.pop(sid, None)

# ============ Agent 主循环 ============
def agent_handle(sid, user_msg):
    """返回 (ok, events)。ok=True 表示 Agent 已处理；events 为 [(type, text), ...]"""
    _clean_sessions()
    events = []

    # 1. 意图预过滤
    blocked, reply = prefilter(user_msg)
    if blocked:
        return True, [('delta', reply)]

    # 2. 构造上下文
    sess = _get_session(sid)
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    for h in sess['history'][-MAX_HISTORY:]:
        if h['role'] in ('user', 'assistant') and h.get('content', '').strip():
            messages.append({'role': h['role'], 'content': h['content'][:1500]})
    messages.append({'role': 'user', 'content': user_msg[:800]})

    # 3. 工具调用循环
    final_text = None
    for _ in range(MAX_AGENT_ITER):
        msg = llm_call(messages, tools=TOOLS)
        if msg is None:
            return False, events  # LLM 不可用 → 降级规则引擎
        tool_calls = msg.get('tool_calls') or []
        if not tool_calls:
            final_text = (msg.get('content') or '').strip()
            break
        # 记录助手消息（含工具调用）
        messages.append({'role': 'assistant', 'content': msg.get('content') or '', 'tool_calls': tool_calls})
        for tc in tool_calls:
            fn = tc.get('function', {})
            name = fn.get('name', '')
            args_str = fn.get('arguments', '')
            events.append(('status', '正在查询' + _tool_label(name) + '…'))
            result = _execute_tool(name, args_str)
            messages.append({'role': 'tool', 'tool_call_id': tc.get('id', ''), 'name': name, 'content': result})

    if final_text is None:
        final_text = '这个问题有点复杂，我换个方式帮你查一下，或者你换个问法试试？'

    # 4. 更新会话历史
    sess['history'].append({'role': 'user', 'content': user_msg})
    sess['history'].append({'role': 'assistant', 'content': final_text})
    if len(sess['history']) > MAX_HISTORY:
        sess['history'] = sess['history'][-MAX_HISTORY:]

    events.append(('delta', final_text))
    return True, events

def _tool_label(name):
    labels = {'search_books': '馆藏书目', 'get_book_borrow_stats': '借阅记录',
              'get_collection_stats': '馆藏统计', 'get_top_borrowed': '借阅排行',
              'get_publisher_rank': '出版社数据', 'get_category_stats': '图书分类',
              'get_gate_stats': '门禁数据', 'get_seat_stats': '座位数据',
              'get_room_stats': '研讨室数据', 'get_exam_stats': '备考联盟数据',
              'get_reader_profile': '读者数据', 'get_scholar_stats': '学者库',
              'search_scholars': '学者论文', 'get_scholar_papers': '学者论文'}
    return labels.get(name, name)

# ============ 规则引擎（离线降级保底） ============
INTENTS = [
    ('hot_books', ['借阅榜', '借得最多', '最受欢迎', '畅销书', '被借最多', '借阅排行', '热门图书', '热门书', '最多人借阅']),
    ('collection_total', ['多少本', '藏书', '馆藏总量', '一共多少', '有多少书', '馆藏规模']),
    ('category', ['分类', '哪一类', '图书分类', '中图法', '类别分布']),
    ('publisher', ['出版社', '出版量', '出版排行']),
    ('book_search', ['有没有', '有这本', '找书', '查找', '查一下', '馆藏中']),
    ('book_borrow_cnt', ['借了几次', '借阅次数', '被借多少']),
    ('gate_dept', ['学院', '院系', '入馆排名', '入馆最多']),
    ('gate_hour', ['几点', '时段', '高峰', '什么时候入馆']),
    ('gate_gender', ['性别', '男女']),
    ('gate_week', ['周几', '星期']),
    ('seat_area', ['座位', '区域', '楼层']),
    ('seat_time', ['预约时段']),
    ('room', ['研讨室']),
    ('exam', ['考研', '考公', '备考', '考编', '出国']),
    ('reader', ['读者', '本科生', '研究生', '构成']),
    ('scholar', ['学者', '论文', '期刊', '科研成果']),
    ('review', ['书评', '读后感', '评价一下', '怎么评价', '推荐语', '写一段']),
    ('recommend', ['推荐', '有什么书', '适合我', '该读', '值得读', '想看']),
]

def parse_intent(text):
    text = text.lower().replace(' ', '')
    for intent, kws in INTENTS:
        for kw in kws:
            if kw in text:
                return intent
    return 'help'

def _fallback_answer(text):
    """规则引擎离线回答（Agent 不可用时的保底）"""
    intent = parse_intent(text)
    try:
        if intent == 'hot_books':
            data = top_borrowed_books(10)
            lines = [f'🏆 嘉庚馆借阅榜 TOP10（真实借阅流水 {len(_load_borrow())} 条）']
            for i, d in enumerate(data, 1):
                lines.append(f'{i}. 《{d["title"]}》—— 被借 {d["count"]} 次')
            return '\n'.join(lines)
        if intent == 'collection_total':
            s = collection_stats()
            lines = [f'📚 馆藏总量：{s["total"]:,} 册']
            lines.append('语种：' + '、'.join(f'{k} {v:,}册' for k, v in list(s['language'].items())[:3]))
            lines.append('文献类型：' + '、'.join(f'{k} {v:,}册' for k, v in list(s['doctype'].items())[:3]))
            if s['year_min']:
                lines.append(f'入藏年份跨度：{s["year_min"]}–{s["year_max"]}')
            return '\n'.join(lines)
        if intent == 'category':
            data = collection_category()
            lines = ['🔖 馆藏分类（中图法）TOP12：']
            for d in data:
                lines.append(f'  {d["cat"]}类：{d["count"]:,} 册')
            return '\n'.join(lines)
        if intent == 'publisher':
            data = publisher_top(10)
            lines = ['🏢 出版社馆藏量 TOP10：']
            for i, d in enumerate(data, 1):
                lines.append(f'{i}. {d["publisher"]} —— {d["count"]:,} 册')
            return '\n'.join(lines)
        if intent == 'book_search':
            kw = re.sub(r'(有没有|有这本|找书|查找|查一下|馆藏中|的?书|吗|呢|？|\?)', '', text)
            kw = kw.strip().strip('《》') or text
            data = search_book(kw[:15])
            if not data:
                return f'🔍 在馆藏目录中未找到包含「{kw}」的图书，可换个关键词试试。'
            lines = [f'🔍 找到 {len(data)} 条与「{kw}」相关的馆藏：']
            for d in data[:6]:
                lines.append(f'  《{d["title"][:30]}》 {d["author"]}（{d["publisher"]} {d["year"]}）')
            return '\n'.join(lines)
        if intent == 'book_borrow_cnt':
            kw = re.sub(r'(借了几次|借阅次数|被借多少|的?书|吗|呢|？|\?)', '', text)
            kw = kw.strip().strip('《》') or text
            r = borrow_stats_by_title(kw[:15])
            if not r:
                return f'📭 借阅流水里没找到含「{kw}」的借阅记录。'
            return f'📖 《{kw}》相关图书共被借阅 {r["borrow_count"]} 次，最常借出的出版社：{r["most_publisher"]}'
        if intent == 'gate_dept':
            data = gate_department_rank(10)
            lines = ['🚪 各学院入馆人次 TOP10：']
            for i, d in enumerate(data, 1):
                lines.append(f'{i}. {d["department"]} —— {d["count"]:,} 人次')
            return '\n'.join(lines)
        if intent == 'gate_hour':
            data = gate_hour_dist()
            peak = max(data, key=lambda x: x['count'])
            lines = [f'🕐 入馆最集中的时段：{peak["hour"]} 点（{peak["count"]:,} 人次）']
            lines.append('全天分布（按小时）：')
            lines.append(' '.join(f'{d["hour"]}点:{d["count"]}' for d in data if d['count'] > 0))
            return '\n'.join(lines)
        if intent == 'gate_gender':
            g = gate_gender_ratio()
            m_r = g['male'] / g['total'] * 100 if g['total'] else 0
            return (f'👥 门禁刷卡记录：男生 {g["male"]:,} 人次（{m_r:.1f}%），女生 {g["female"]:,} 人次（{100 - m_r:.1f}%），'
                    f'合计 {g["total"]:,} 人次')
        if intent == 'gate_week':
            data = gate_peak_weeks()
            peak = max(data, key=lambda x: x['count'])
            return f'📅 入馆高峰是{peak["weekday"]}（{peak["count"]:,} 人次）。' + '；'.join(
                f'{d["weekday"]} {d["count"]:,}' for d in data)
        if intent == 'seat_area':
            data = seat_hot_area()
            lines = ['🪑 座位预约热门区域 TOP8：']
            for i, d in enumerate(data, 1):
                lines.append(f'{i}. {d["floor"]}楼{d["area"]}区 —— {d["count"]:,} 次预约')
            return '\n'.join(lines)
        if intent == 'seat_time':
            data = seat_time_dist()
            lines = ['⏰ 座位预约最热时段 TOP5：']
            for i, d in enumerate(data[:5], 1):
                lines.append(f'{i}. {d["slot"]} —— {d["count"]:,} 次')
            return '\n'.join(lines)
        if intent == 'room':
            data = room_hot()
            lines = ['🏢 研讨室使用热度 TOP8：']
            for i, d in enumerate(data, 1):
                lines.append(f'{i}. {d["room"]} —— {d["count"]:,} 次预约')
            return '\n'.join(lines)
        if intent == 'exam':
            s = exam_stats()
            lines = ['🎯 备考联盟预约统计：']
            for d in s['types']:
                lines.append(f'  {d["type"]}：{d["count"]:,} 人次')
            lines.append(f'  性别：男 {s["gender"]["male"]:,} / 女 {s["gender"]["female"]:,}')
            return '\n'.join(lines)
        if intent == 'reader':
            p = reader_profile()
            lines = ['👤 读者构成：']
            for k, v in p['type'].items():
                lines.append(f'  {k}：{v:,} 人')
            lines.append('  院系分布 TOP5：' + '、'.join(f'{d["dept"]} {d["count"]}' for d in p['department'][:5]))
            return '\n'.join(lines)
        if intent == 'scholar':
            s = scholar_stats()
            lines = [f'🎓 学者库收录嘉庚学者论文 {s["total_papers"]:,} 篇']
            lines.append('  高产期刊：' + '、'.join(f'{d["journal"]}（{d["count"]}）' for d in s['journals']))
            return '\n'.join(lines)
        if intent == 'review':
            kw = re.sub(r'(书评|读后感|评价一下|怎么评价|推荐语|写一段|的?书|吗|呢|？|\?)', '', text)
            kw = kw.strip().strip('《》') or text
            r = borrow_stats_by_title(kw[:15])
            extra = f'\n（真实数据：相关图书被借阅 {r["borrow_count"]} 次）' if r else ''
            msg = llm_call([{'role': 'user', 'content':
                             f'请为《{kw}》写一段150字左右的书评，语气自然，像爱读书的学长学姐在推荐。'
                             f'不要编造书籍内容细节，如果没读过就说没读过，转而从馆藏受欢迎程度角度聊。{extra}'}])
            if msg and msg.get('content'):
                return msg['content'].strip() + extra
            return f'📖 暂无法生成书评（LLM未接入），可先查《{kw}》的馆藏与借阅情况。'
        if intent == 'recommend':
            kw = re.sub(r'(推荐|几本|一些|什么书|适合我|该读|值得读|想看|的?书|吗|呢|？|\?)', '', text)
            kw = kw.strip() or '热门'
            df = _load_books()
            m = df[df['TITLE'].astype(str).str.contains(kw, na=False, regex=False)].head(6)
            if len(m) == 0:
                m = df.sample(6, random_state=42)
                source = '（主题匹配少，改为随机抽样馆藏）'
            else:
                source = f'（从馆藏 {len(df):,} 册中按主题「{kw}」检索）'
            books = [f'《{t}》（{a}）' for t, a in zip(m['TITLE'], m['AUTHOR'])]
            prompt = (f'读者想要主题为「{kw}」的图书推荐。馆藏检索到这些书目：\n'
                      + '\n'.join(books) + '\n请挑选3-5本给出推荐，每本一句推荐理由，语气自然不机械。')
            msg = llm_call([{'role': 'user', 'content': prompt}])
            if msg and msg.get('content'):
                return msg['content'].strip() + '\n' + source
            lines = [f'📚 按「{kw}」找到的相关馆藏 {source}：']
            lines += [f'  《{str(t)[:28]}》 {a}' for t, a in zip(m['TITLE'][:5], m['AUTHOR'][:5])]
            return '\n'.join(lines)
    except Exception as e:
        return f'⚠️ 查询出错：{e}（该数据源暂未接入）'
    msg = llm_call([{'role': 'user', 'content': text}])
    if msg and msg.get('content'):
        return msg['content'].strip()
    return HELP_TEXT

HELP_TEXT = """我是「嘉庚智慧馆员」，可以这样问我：
📚 馆藏类：馆藏有多少本书 / 图书分类 / 出版社排行 / 有没有《XX》
🏆 借阅类：最受欢迎的图书 / 《XX》被借了几次
🚪 门禁类：哪个学院入馆最多 / 几点入馆人最多 / 入馆性别比例
🪑 空间类：座位热门区域 / 研讨室热度 / 备考联盟统计
🎓 学者类：学者论文 / 哪些老师研究XX（学术地图）"""

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        q = sys.argv[1]
        ok, evs = agent_handle('cli-test', q)
        print('【Agent处理】', ok)
        for t, x in evs:
            print(f'[{t}]', x)
    else:
        tests = ['最受欢迎的图书', '推荐几本人工智能的书', '我们学校哪些老师研究人工智能',
                 '哪个学院入馆最多', '考研人数', '今天天气怎么样', '你好']
        for q in tests:
            print('❓', q)
            ok, evs = agent_handle('cli-' + q[:6], q)
            for t, x in evs:
                if t == 'status':
                    print('   ⏳', x)
                else:
                    print('   →', x)
            print('-' * 40)
