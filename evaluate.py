"""
POI Agent 综合评估脚本

自动生成 500 条随机 POI 查询，对 agent 进行三维度评估：
  1. 解析准确率 — 地点/类型/条数的识别是否正确
  2. 工具调用成功率 — 地理编码、POI搜索是否成功
  3. 数据质量 — 返回结果的相关性、完整性、距离合理性

用法：先启动 Flask 服务 (python app.py)，再运行 python evaluate.py
"""

import json
import csv
import random
import time
import requests
from datetime import datetime
from collections import defaultdict

# ===== 配置 =====
BASE_URL = "http://127.0.0.1:5000"
AMAP_API_KEY = "3e1379e3c44e93990c9c31dd707b031e"
OUTPUT_CSV = "evaluation_results.csv"
OUTPUT_REPORT = "evaluation_report.json"
TOTAL_QUERIES = 500

# ===== 查询生成素材 =====
LOCATIONS = [
    "南信大", "南京信息工程大学", "新街口", "夫子庙", "鼓楼", "玄武湖",
    "仙林大学城", "浦口", "江宁大学城", "河西万达", "南京南站", "南京站",
    "中山陵", "老门东", "百家湖", "奥体中心", "迈皋桥", "桥北", "龙江",
    "马群", "孝陵卫", "苜蓿园", "大行宫", "三山街", "元通",
]

TYPES = [
    "餐厅", "咖啡店", "奶茶店", "火锅店", "快餐店", "小吃店", "面包店",
    "超市", "便利店", "药店", "医院", "银行", "加油站",
    "充电站", "停车场", "公交站", "地铁站", "酒店", "电影院",
    "健身房", "图书馆", "书店", "学校", "商场", "购物中心",
    "KTV", "邮局", "菜市场", "宾馆",
]

CATEGORIES = [
    "餐饮类", "教育类", "交通类", "购物类", "住宿类", "医疗类", "娱乐类",
    "金融类", "生活服务类",
]

CATEGORY_ALIASES = ["吃的", "美食", "上学", "出行", "买东西", "住", "看病", "玩", "取钱", "生活服务"]

COUNT_PATTERNS = ["{}条", "{}家", "{}个", "{}个结果"]

LOCATION_SUFFIXES = ["附近", "周边", "周围", "旁边", "一带"]

# ===== 1. 查询生成器 =====
def generate_queries(n=500):
    """生成 n 条多样化随机查询，每条附带 ground truth 标签"""
    queries = []
    random.seed(42)

    templates = [
        # (权重, 生成函数)
        (35, gen_complete_query),       # "南信大附近的餐厅"
        (15, gen_complete_with_count),  # "南信大附近5家餐厅"
        (8, gen_vague_poi),             # "南信大附近的poi"
        (10, gen_location_only),        # "南信大附近"
        (10, gen_type_only),            # "咖啡店"
        (7, gen_category_only),         # "餐饮类"
        (5, gen_category_alias),        # "吃的"
        (3, gen_greeting),              # "你好"
        (4, gen_location_with_category),# "南信大附近的餐饮类"
        (3, gen_multi_type),            # "南信大附近的咖啡店和书店"
    ]

    total_weight = sum(w for w, _ in templates)
    for _ in range(n):
        r = random.randint(1, total_weight)
        cumulative = 0
        for weight, gen_func in templates:
            cumulative += weight
            if r <= cumulative:
                queries.append(gen_func())
                break
    return queries


def random_location():
    loc = random.choice(LOCATIONS)
    suffix = random.choice(LOCATION_SUFFIXES)
    return f"{loc}{suffix}", loc


def random_type():
    return random.choice(TYPES)


def random_count():
    n = random.choice([3, 5, 8, 10, 12, 15, 20, 25, 30])
    pattern = random.choice(COUNT_PATTERNS)
    return pattern.format(n), n


def gen_complete_query():
    """生成完整查询: 地点+类型"""
    loc_text, loc_name = random_location()
    typ = random_type()
    query = f"{loc_text}的{typ}"
    return {
        "query": query,
        "expected_location": loc_name,
        "expected_has_type": True,
        "expected_has_count": False,
        "expected_type": typ,
        "category": "complete",
        "is_greeting": False,
    }


def gen_complete_with_count():
    """生成带条数的完整查询"""
    loc_text, loc_name = random_location()
    typ = random_type()
    count_text, count_num = random_count()
    query = f"{loc_text}的{count_text}{typ}"
    return {
        "query": query,
        "expected_location": loc_name,
        "expected_has_type": True,
        "expected_has_count": True,
        "expected_type": typ,
        "expected_count": count_num,
        "category": "complete_with_count",
        "is_greeting": False,
    }


def gen_vague_poi():
    """生成模糊查询: 地点 + 泛称"""
    loc_text, loc_name = random_location()
    vague_word = random.choice(["poi", "POI", "有什么", "有什么好玩的", "有啥"])
    query = f"{loc_text}{vague_word}"
    return {
        "query": query,
        "expected_location": loc_name,
        "expected_has_type": False,
        "expected_has_count": False,
        "category": "vague",
        "is_greeting": False,
    }


def gen_location_only():
    """仅地点"""
    loc_text, loc_name = random_location()
    return {
        "query": loc_text,
        "expected_location": loc_name,
        "expected_has_type": False,
        "expected_has_count": False,
        "category": "location_only",
        "is_greeting": False,
    }


def gen_type_only():
    """仅类型"""
    typ = random_type()
    return {
        "query": typ,
        "expected_location": None,
        "expected_has_type": True,
        "expected_has_count": False,
        "expected_type": typ,
        "category": "type_only",
        "is_greeting": False,
    }


def gen_category_only():
    """仅分类"""
    cat = random.choice(CATEGORIES)
    return {
        "query": cat,
        "expected_location": None,
        "expected_has_type": True,
        "expected_has_count": False,
        "expected_category": cat,
        "category": "category_only",
        "is_greeting": False,
    }


def gen_category_alias():
    """分类别名"""
    alias = random.choice(CATEGORY_ALIASES)
    return {
        "query": alias,
        "expected_location": None,
        "expected_has_type": True,
        "expected_has_count": False,
        "category": "category_alias",
        "is_greeting": False,
    }


def gen_greeting():
    """问候语"""
    g = random.choice(["你好", "您好", "嗨", "在吗", "谢谢", "hello", "hi"])
    return {
        "query": g,
        "expected_location": None,
        "expected_has_type": False,
        "expected_has_count": False,
        "category": "greeting",
        "is_greeting": True,
    }


def gen_location_with_category():
    """地点 + 大类"""
    loc_text, loc_name = random_location()
    cat = random.choice(CATEGORIES)
    query = f"{loc_text}的{cat}"
    return {
        "query": query,
        "expected_location": loc_name,
        "expected_has_type": True,
        "expected_has_count": False,
        "expected_category": cat,
        "category": "location_with_category",
        "is_greeting": False,
    }


def gen_multi_type():
    """多类型查询"""
    loc_text, loc_name = random_location()
    t1 = random.choice(TYPES)
    t2 = random.choice([t for t in TYPES if t != t1])
    query = f"{loc_text}的{t1}和{t2}"
    return {
        "query": query,
        "expected_location": loc_name,
        "expected_has_type": True,
        "expected_has_count": False,
        "expected_type": t1,
        "category": "multi_type",
        "is_greeting": False,
    }


# ===== 2. 评估执行器 =====
def haversine(lon1, lat1, lon2, lat2):
    """计算两点间距离（米）"""
    from math import radians, cos, sin, asin, sqrt
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371000


def geocode(address, city="南京"):
    """地理编码"""
    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {"key": AMAP_API_KEY, "address": address, "city": city}
    try:
        resp = requests.get(url, params=params, timeout=5).json()
        if resp["status"] == "1" and resp["geocodes"]:
            loc = resp["geocodes"][0]["location"]
            return loc
    except:
        pass
    return None


def test_single_query(gt, index):
    """测试单条查询，返回评估结果"""
    result = {
        "index": index,
        "query": gt["query"],
        "category": gt["category"],
        # 解析维度
        "parse_state": None,
        "parse_location_match": None,
        "parse_type_detected": None,
        "parse_count_detected": None,
        "parse_correct": None,
        # 工具调用维度
        "geocode_success": None,
        "search_success": None,
        "search_result_count": 0,
        "api_error": None,
        # 数据质量维度
        "data_has_names": 0,
        "data_has_addresses": 0,
        "data_has_coords": 0,
        "data_avg_distance_km": None,
        "data_type_relevance": 0,
        "data_quality_score": 0,
        # 综合
        "overall_score": 0,
        "response_type": None,
        "total_turns": 1,
    }

    session_id = f"eval_{index}_{random.randint(1000, 9999)}"

    try:
        # Turn 1: 发送用户查询
        r = requests.post(f"{BASE_URL}/chat",
                          json={"message": gt["query"], "session_id": session_id},
                          timeout=30)
        if r.status_code != 200:
            result["api_error"] = f"HTTP {r.status_code}"
            return result

        data = r.json()
        response_type = data.get("type", "error")
        result["response_type"] = response_type
        result["parse_state"] = data.get("context", {}).get("state", "unknown")

        # === 解析准确率评估 ===
        ctx = data.get("context", {})

        # 地点识别
        if gt.get("expected_location"):
            loc_desc = ctx.get("location_desc", "")
            result["parse_location_match"] = (
                gt["expected_location"] in loc_desc or gt["expected_location"] in data.get("message", "")
            )

        # 类型检测
        if gt.get("expected_has_type"):
            keywords = ctx.get("keywords", [])
            result["parse_type_detected"] = len(keywords) > 0
        else:
            result["parse_type_detected"] = True  # 不应检测到类型时默认为正确

        # 条数检测
        if gt.get("expected_has_count") and response_type == "results":
            result["parse_count_detected"] = True

        # 整体解析正确性
        if gt["is_greeting"]:
            result["parse_correct"] = (response_type == "follow_up")
        elif gt["category"] in ("type_only", "category_only", "category_alias"):
            result["parse_correct"] = (response_type == "follow_up")
        elif gt["category"] in ("location_only", "vague"):
            result["parse_correct"] = (response_type == "follow_up" and
                                        result["parse_state"] == "awaiting_type")
        elif gt["category"] in ("complete", "complete_with_count", "location_with_category", "multi_type"):
            result["parse_correct"] = response_type in ("follow_up", "results")
        else:
            result["parse_correct"] = (response_type != "error")

        # If follow_up (awaiting count), do turn 2
        if (response_type == "follow_up" and
            result["parse_state"] == "awaiting_count" and
                gt["category"] in ("complete", "complete_with_count", "location_with_category", "multi_type")):
            result["total_turns"] = 2
            count_msg = str(gt.get("expected_count", 10)) + "条"
            r2 = requests.post(f"{BASE_URL}/chat",
                               json={"message": count_msg, "session_id": session_id},
                               timeout=30)
            if r2.status_code == 200:
                data = r2.json()
                response_type = data.get("type", "error")
                result["response_type"] = response_type
                result["parse_state"] = data.get("context", {}).get("state", "unknown")

        # === 工具调用 & 数据质量评估 ===
        if response_type == "results":
            results_list = data.get("results", [])
            result["search_result_count"] = len(results_list)
            result["search_success"] = len(results_list) > 0

            if results_list:
                # 地理编码成功推断：有地点描述 + 有搜索结果 → geocode 成功
                loc_desc = data.get("context", {}).get("location_desc", "")
                result["geocode_success"] = bool(loc_desc) and len(results_list) > 0

                # 数据完整性
                has_name = sum(1 for p in results_list if p.get("name"))
                has_addr = sum(1 for p in results_list if p.get("address") and p["address"] != "暂无地址")
                has_coords = sum(1 for p in results_list if p.get("lng") and p.get("lat"))
                result["data_has_names"] = has_name
                result["data_has_addresses"] = has_addr
                result["data_has_coords"] = has_coords

                # 数据完整性得分 (0-100)
                n = len(results_list)
                if n > 0:
                    result["data_quality_score"] = int(
                        (has_name / n * 40) + (has_addr / n * 30) + (has_coords / n * 30)
                    )

                # 距离合理性：直接对 expected_location 做地理编码
                if gt.get("expected_location"):
                    target_loc = geocode(gt["expected_location"])
                    if target_loc:
                        t_lng, t_lat = map(float, target_loc.split(","))
                        distances = []
                        for p in results_list:
                            try:
                                p_lng = float(p.get("lng", 0))
                                p_lat = float(p.get("lat", 0))
                                if p_lng and p_lat:
                                    d = haversine(t_lng, t_lat, p_lng, p_lat)
                                    distances.append(d)
                            except:
                                pass
                        if distances:
                            result["data_avg_distance_km"] = round(sum(distances) / len(distances) / 1000, 2)

                # 类型相关性：POI类型与查询关键词的匹配度
                if gt.get("expected_type"):
                    relevant = 0
                    for p in results_list:
                        ptype = p.get("type", "")
                        pname = p.get("name", "")
                        if gt["expected_type"] in ptype or gt["expected_type"] in pname:
                            relevant += 1
                    result["data_type_relevance"] = int(relevant / n * 100) if n > 0 else 0

            # 计算综合得分
            parse_score = 100 if result["parse_correct"] else 0
            tool_score = 0
            if result["geocode_success"]:
                tool_score += 50
            if result["search_success"]:
                tool_score += 50
            quality_score = result["data_quality_score"]
            result["overall_score"] = int(parse_score * 0.3 + tool_score * 0.3 + quality_score * 0.4)

    except requests.exceptions.ConnectionError:
        result["api_error"] = "Connection refused — 请先启动 Flask 服务"
    except Exception as e:
        result["api_error"] = str(e)[:200]

    return result


# ===== 3. 主评估流程 =====
def run_evaluation():
    print("=" * 70)
    print("  POI Agent 综合评估")
    print(f"  生成 {TOTAL_QUERIES} 条随机查询 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 生成查询
    print("\n[1/4] 生成测试查询...")
    test_queries = generate_queries(TOTAL_QUERIES)
    print(f"  生成 {len(test_queries)} 条查询")

    # 统计类别分布
    cat_dist = defaultdict(int)
    for q in test_queries:
        cat_dist[q["category"]] += 1
    print("  类别分布:")
    for cat, count in sorted(cat_dist.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count} ({count/TOTAL_QUERIES*100:.1f}%)")

    # 执行测试
    print(f"\n[2/4] 执行测试 (调用 /chat 端点)...")
    results = []
    parse_correct = 0
    search_success = 0
    total_results = 0
    errors = 0
    start_time = time.time()

    for i, query in enumerate(test_queries, 1):
        res = test_single_query(query, i)
        results.append(res)

        if res["parse_correct"]:
            parse_correct += 1
        if res["search_success"]:
            search_success += 1
            total_results += res["search_result_count"]
        if res["api_error"]:
            errors += 1

        # 进度条
        if i % 50 == 0 or i == TOTAL_QUERIES:
            elapsed = time.time() - start_time
            eta = elapsed / i * (TOTAL_QUERIES - i)
            print(f"  进度: {i}/{TOTAL_QUERIES} ({i/TOTAL_QUERIES*100:.0f}%) | "
                  f"耗时: {elapsed:.0f}s | 预计剩余: {eta:.0f}s | 错误: {errors}")

    elapsed_total = time.time() - start_time
    print(f"  完成！总耗时: {elapsed_total:.0f}s ({elapsed_total/60:.1f}min)")

    # 保存详细结果 CSV
    print(f"\n[3/4] 保存结果...")
    fieldnames = [
        "index", "query", "category", "response_type", "parse_state",
        "parse_correct", "parse_location_match", "parse_type_detected",
        "geocode_success", "search_success", "search_result_count",
        "data_has_names", "data_has_addresses", "data_has_coords",
        "data_avg_distance_km", "data_type_relevance", "data_quality_score",
        "overall_score", "total_turns", "api_error"
    ]
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for r in results:
            writer.writerow(r)
    print(f"  CSV 已保存: {OUTPUT_CSV}")

    # ===== 4. 统计报告 =====
    print(f"\n[4/4] 生成评估报告...")
    total = len(results)
    searches_with_results = [r for r in results if r["response_type"] == "results"]
    searches_count = len(searches_with_results)

    # 解析准确率 (按类别)
    parse_by_cat = defaultdict(lambda: {"total": 0, "correct": 0})
    for r in results:
        cat = r["category"]
        parse_by_cat[cat]["total"] += 1
        if r["parse_correct"]:
            parse_by_cat[cat]["correct"] += 1

    # 工具调用成功率
    geocode_ok = sum(1 for r in results if r["geocode_success"])
    search_ok = sum(1 for r in results if r["search_success"])

    # 数据质量
    avg_distance = []
    type_relevances = []
    quality_scores = []
    for r in searches_with_results:
        if r["data_avg_distance_km"]:
            avg_distance.append(r["data_avg_distance_km"])
        if r["data_type_relevance"] is not None:
            type_relevances.append(r["data_type_relevance"])
        quality_scores.append(r["data_quality_score"])

    report = {
        "title": "POI Agent 评估报告",
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_queries": total,
            "total_errors": errors,
            "avg_response_time_s": round(elapsed_total / total, 2),
            # 解析准确率
            "parse_accuracy": round(parse_correct / total * 100, 1),
            # 工具调用成功率
            "geocode_success_rate": round(geocode_ok / total * 100, 1) if total > 0 else 0,
            "search_success_rate": round(search_ok / searches_count * 100, 1) if searches_count > 0 else 0,
            # 数据质量
            "total_poi_returned": total_results,
            "avg_results_per_search": round(total_results / searches_count, 1) if searches_count > 0 else 0,
            "avg_distance_km": round(sum(avg_distance) / len(avg_distance), 2) if avg_distance else None,
            "avg_type_relevance_pct": round(sum(type_relevances) / len(type_relevances), 1) if type_relevances else None,
            "avg_data_quality_score": round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else None,
            # 综合得分
            "overall_score": round(
                sum(r["overall_score"] for r in results) / total, 1
            ) if total > 0 else 0,
        },
        "parse_accuracy_by_category": {
            cat: {
                "total": v["total"],
                "correct": v["correct"],
                "rate": round(v["correct"] / v["total"] * 100, 1)
            }
            for cat, v in sorted(parse_by_cat.items(), key=lambda x: -x[1]["correct"] / max(x[1]["total"], 1))
        },
        "response_type_distribution": {
            str(t): sum(1 for r in results if r["response_type"] == t)
            for t in set(r["response_type"] for r in results)
        },
        "state_distribution": {
            str(s): sum(1 for r in results if r["parse_state"] == s)
            for s in set(r["parse_state"] for r in results)
        },
    }

    with open(OUTPUT_REPORT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  报告已保存: {OUTPUT_REPORT}")

    # 打印总结
    s = report["summary"]
    print("\n" + "=" * 70)
    print("  评 估 总 结")
    print("=" * 70)
    print(f"  测试查询数:        {total}")
    print(f"  平均响应时间:      {s['avg_response_time_s']}s/条")
    print(f"  错误数:            {errors}")
    print("")
    print(f"  [解析] 准确率:          {s['parse_accuracy']}%")
    print(f"  [工具] 地理编码成功率:   {s['geocode_success_rate']}%")
    print(f"  [工具] POI搜索成功率:    {s['search_success_rate']}% ({searches_count}次搜索)")
    print("")
    print(f"  [质量] 返回POI总数:      {s['total_poi_returned']}")
    print(f"  [质量] 每次搜索平均:     {s['avg_results_per_search']}条")
    if s['avg_distance_km']:
        print(f"  [质量] 平均距离:         {s['avg_distance_km']}km")
    if s['avg_type_relevance_pct']:
        print(f"  [质量] 类型相关性:       {s['avg_type_relevance_pct']}%")
    print(f"  [质量] 数据完整性得分:   {s['avg_data_quality_score']}/100")
    print("")
    print(f"  [综合] 总分:             {s['overall_score']}/100")
    print(f"")
    print(f"  详细结果: {OUTPUT_CSV}")
    print(f"  评估报告: {OUTPUT_REPORT}")
    print("=" * 70)

    return report


if __name__ == '__main__':
    # 检查服务是否可用
    try:
        r = requests.get(f"{BASE_URL}/", timeout=3)
        if r.status_code != 200:
            print("[WARN] 服务响应异常，请确保 Flask 已正常启动")
    except requests.exceptions.ConnectionError:
        print("[ERROR] 无法连接到 Flask 服务！")
        print("   请先运行: python app.py")
        print("   然后再执行: python evaluate.py")
        exit(1)

    run_evaluation()
