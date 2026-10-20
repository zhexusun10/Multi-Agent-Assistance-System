"""Sparse retrieval vocabulary, separate from the source documents and intent routing."""
import re
import jieba

# Cross-language terms expand lexical recall; documents are still ranked by BM25 body statistics.
SYNONYMS = {
    '押金': ['deposit', '保证金', '押一', '退押'],
    '租期': ['lease', 'tenancy', '最短', '短租', '租多久', '几个月'],
    '组屋': ['hdb'], '公寓': ['condo', 'condominium', '私人住宅'],
    '印花税': ['stamp duty', 'stamping', '盖印'],
    '准证': ['pass', 'visa', '签证', '资格', 'student'],
    '家具': ['furniture', 'furnished', '床垫', '衣柜'],
    '水电': ['utilities', '电费', '水费', '网费'],
    '佣金': ['commission', '中介费', '经纪费'],
    '诈骗': ['scam', '冒充', '骗钱', '防骗'],
    '卫浴': ['bathroom', '卫生间', '洗手间', '厕所'],
    '距离': ['geodesic', '直线', '步行', 'commute'],
    '待核实': ['unknown', '未知', '缺失'],
    '看房': ['viewing', '预约', '交接'],
    '回复': ['reply', 'respond', '粘贴'],
    '索偿': ['sct', 'tribunal', '纠纷', '争议'],
}
STOP = {'的','了','是','在','我','你','请问','一下','可以','什么','怎么','the','a','is','to','of','and','in','do','how'}


def tokenize(text):
    text = text.lower()
    result = [t for t in jieba.cut(text) if t.strip() and re.search(r'[\w\u4e00-\u9fff]', t) and t not in STOP]
    for canonical, words in SYNONYMS.items():
        if canonical in text or any(re.search(r'(?<![a-z])'+re.escape(w)+r'(?![a-z])', text) if w.isascii() else w in text for w in words):
            result.append(canonical)
    return result
