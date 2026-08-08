"""测试 API 可访问性：东方财富研报、arXiv 论文。"""
import requests
import json

# 1. 测试东方财富研报API
print("=== 测试东方财富研报API ===")
try:
    url = "https://reportapi.eastmoney.com/report/list"
    params = {
        "cb": "",
        "pageSize": 3,
        "industryCode": "*",
        "pageNo": 1,
        "reportType": 1,
        "columnsType": 1,
        "source": "WEB",
        "client": "WEB",
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, timeout=15, headers=headers)
    print(f"   Status: {r.status_code}")
    data = r.json()
    reports = data.get("data", [])
    print(f"   Reports count: {len(reports)}")
    for rep in reports[:2]:
        title = rep.get("title", "?")
        industry = rep.get("industryName", "?")
        stock = rep.get("stockName", "?")
        print(f"   - {title} | {industry} | {stock}")
except Exception as e:
    print(f"   ERROR: {e}")

# 2. 测试arXiv API
print()
print("=== 测试arXiv API ===")
try:
    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": "cat:q-fin.ST",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": 3,
    }
    r = requests.get(url, params=params, timeout=30)
    print(f"   Status: {r.status_code}")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(r.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    print(f"   Papers: {len(entries)}")
    for entry in entries:
        title = entry.find("atom:title", ns)
        txt = title.text.strip()[:100] if title is not None else "?"
        print(f"   - {txt}...")
except Exception as e:
    print(f"   ERROR: {e}")

# 3. 测试 arXiv 综合金融 + 统计学习
print()
print("=== 测试arXiv q-fin.GN (General Finance) ===")
try:
    params = {
        "search_query": "cat:q-fin.GN",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": 3,
    }
    r = requests.get("https://export.arxiv.org/api/query", params=params, timeout=30)
    print(f"   Status: {r.status_code}")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(r.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    print(f"   Papers: {len(entries)}")
    for entry in entries:
        title = entry.find("atom:title", ns)
        txt = title.text.strip()[:100] if title is not None else "?"
        print(f"   - {txt}...")
except Exception as e:
    print(f"   ERROR: {e}")