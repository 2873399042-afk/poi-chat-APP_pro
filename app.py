from flask import Flask, request, render_template, jsonify
import re
import requests
from dashscope import Generation
import dashscope
from math import radians, cos, sin, asin, sqrt

# 初始化 Flask 应用
app = Flask(__name__)

# === 配置 API Keys ===
DASHSCOPE_API_KEY = "sk-9c5f6447cd3b48deb6ee603141759be6"  # Qwen Key
AMAP_API_KEY = "3e1379e3c44e93990c9c31dd707b031e"  # 高德 Key
dashscope.api_key = DASHSCOPE_API_KEY

# 默认兜底城市
DEFAULT_CITY = "南京"


def parse_query_with_context(user_query, last_city=DEFAULT_CITY):
    """使用 Qwen 解析用户自然语言查询，强制 keyword 在标准 POI 白名单中"""

    # ✅ 核心鲁棒性改进：定义标准 POI 类型白名单
    POI_WHITELIST = [
        "咖啡店", "奶茶店", "餐厅", "快餐店", "小吃店", "火锅店", "面包店",
        "超市", "便利店", "药店", "医院", "诊所", "社区卫生服务中心",
        "银行", "ATM", "加油站", "充电站", "停车场", "公交站", "地铁站",
        "火车站", "高铁站", "机场", "酒店", "宾馆", "电影院", "KTV",
        "健身房", "体育馆", "图书馆", "书店", "学校", "小学", "中学", "大学",
        "派出所", "消防站", "邮局", "快递点", "菜市场", "商场", "购物中心"
    ]

    whitelist_str = "、".join(POI_WHITELIST)

    prompt = f"""
你是一个地理信息助手，请输出纯 JSON，仅包含字段：city, keyword, location_desc。
规则：
1. 如果未提及城市，使用上下文城市：{last_city}
2. keyword 必须从以下列表中选择（严格匹配）：
   {whitelist_str}
3. 如果用户意图模糊（如“附近有啥”“有什么地方”），优先猜测高频需求：
   - 白天 → 咖啡店、餐厅、便利店
   - 晚上 → 餐厅、KTV
   - 提到“买药”“不舒服” → 药店、医院
   - 提到“停车”“加油” → 停车场、加油站
4. location_desc 只填具体地点名（如“新街口”“中关村”），不要填“附近”

用户查询: "{user_query}"
输出（纯 JSON）:
"""

    try:
        response = Generation.call(
            model="qwen-plus",
            prompt=prompt,
            result_format="message"
        )
        raw = response.output.choices[0].message.content.strip()
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            parsed = eval(json_match.group(0))
            #  强制 keyword 在白名单内
            if parsed.get("keyword") not in POI_WHITELIST:
                parsed["keyword"] = extract_fallback_keyword(user_query)
            return parsed
        else:
            raise ValueError("No JSON found")
    except Exception as e:
        print(f"[Qwen 解析失败] {e}")
        return {
            "city": last_city,
            "keyword": extract_fallback_keyword(user_query),
            "location_desc": ""
        }


def extract_fallback_keyword(query):
    """关键词回退策略（与 POI 白名单对齐）"""
    query = query.lower()
    rules = {
        "喝": "咖啡店", "咖啡": "咖啡店", "奶茶": "奶茶店", "茶": "奶茶店",
        "吃": "餐厅", "饭": "餐厅", "菜": "餐厅", "火锅": "火锅店", "面": "餐厅",
        "药": "药店", "医": "医院", "病": "医院", "不舒服": "医院",
        "车": "停车场", "停": "停车场", "停车": "停车场", "加油": "加油站",
        "住": "酒店", "宿": "酒店", "旅馆": "酒店",
        "电影": "电影院", "玩": "KTV", "唱": "KTV", "歌": "KTV",
        "书": "书店", "银行": "银行", "取钱": "ATM", "存钱": "银行",
        "超": "超市", "便利": "便利店", "学": "学校", "校": "学校",
        "健身": "健身房", "锻炼": "健身房"
    }
    for word, kw in rules.items():
        if word in query:
            return kw
    return "餐厅"  # 默认比“商店”更合理


def geocode_address(address, city):
    """地理编码：将地址转为经纬度"""
    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {
        "key": AMAP_API_KEY,
        "address": address,
        "city": city,
        "output": "json"
    }
    try:
        resp = requests.get(url, params=params, timeout=5).json()
        if resp["status"] == "1" and resp["geocodes"]:
            return resp["geocodes"][0]["location"]
    except Exception as e:
        print(f"[Geocode 失败] {e}")
    return ""


def search_poi(city, keyword, center=""):
    """调用高德 POI 搜索接口，支持空结果 fallback"""

    def _do_search(kw, use_around=True):
        if center and use_around:
            url = "https://restapi.amap.com/v3/place/around"
            params = {
                "key": AMAP_API_KEY,
                "keywords": kw,
                "location": center,
                "radius": 3000,
                "offset": 15,
                "extensions": "all",
                "output": "json"
            }
        else:
            url = "https://restapi.amap.com/v3/place/text"
            params = {
                "key": AMAP_API_KEY,
                "keywords": kw,
                "city": city,
                "offset": 15,
                "extensions": "all",
                "output": "json"
            }
        try:
            resp = requests.get(url, params=params, timeout=5).json()
            if resp.get("status") == "1":
                return resp.get("pois", [])
        except Exception as e:
            print(f"[POI 搜索失败] {e}")
        return []

    # 第一次搜索
    results = _do_search(keyword, use_around=bool(center))

    # 如果结果为空，尝试 fallback 策略
    if not results:
        print(f"[Fallback 触发] 原 keyword: {keyword}")

        # Fallback 1: 如果是 around 搜索失败，降级为 text 搜索（扩大范围）
        if center:
            results = _do_search(keyword, use_around=False)
        # Fallback 2: 如果仍为空，用更通用的同义词
        if not results:
            synonym_map = {
                "奶茶店": ["饮品店", "茶饮"],
                "咖啡店": ["饮品店", "咖啡"],
                "餐厅": ["美食", "饭店", "餐饮"],
                "快餐店": ["美食", "简餐"],
                "小吃店": ["美食", "小吃"],
                "便利店": ["超市", "商店"],
                "药店": ["药房", "医药"],
                "医院": ["医疗机构", "诊所"],
                "充电站": ["充电桩", "新能源充电"],
                "地铁站": ["轨道交通", "地铁"],
                "火车站": ["铁路车站", "火车"],
                "学校": ["教育机构", "校区"]
            }
            for alt_kw in synonym_map.get(keyword, ["餐厅", "商店"]):
                results = _do_search(alt_kw, use_around=False)
                if results:
                    print(f"  → 使用同义词 '{alt_kw}' 成功召回")
                    break

    return results


def haversine(lon1, lat1, lon2, lat2):
    """计算两点之间球面距离（单位：米）"""
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    r = 6371000  # 地球半径（米）
    return c * r


def generate_density_insight(pois, radius_m=500):
    """增强版密度洞察"""
    points = []
    valid_pois = []

    for poi in pois:
        try:
            location = poi.get("location", "")
            if not location or "," not in location:
                continue
            lng_str, lat_str = location.split(",", 1)
            lng = float(lng_str.strip())
            lat = float(lat_str.strip())
            points.append((lng, lat))
            valid_pois.append(poi)
        except (ValueError, TypeError, AttributeError):
            continue

    if len(points) < 2:
        return "暂无法分析空间分布特征。"

    # 计算每个点的局部密度
    densities = []
    for i in range(len(points)):
        count = 0
        for j in range(len(points)):
            dist = haversine(points[i][0], points[i][1], points[j][0], points[j][1])
            if dist <= radius_m:
                count += 1
        densities.append(count)

    avg_density = sum(densities) / len(densities)

    if avg_density >= 5:
        desc = "高度密集，商业氛围浓厚"
    elif avg_density >= 3:
        desc = "分布较密集，选择丰富"
    elif avg_density >= 2:
        desc = "分布较为稀疏，环境安静"
    else:
        desc = "极为稀疏，具有独占性"

    keyword = valid_pois[0].get('type', '目标设施') if valid_pois else '目标设施'
    return f"🌐 空间洞察：该区域{keyword}分布{desc}（平均 {avg_density:.1f} 家/{radius_m}m 半径）。"


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/search', methods=['POST'])
def search():
    data = request.get_json()
    user_query = data.get("query", "").strip()

    if not user_query:
        return jsonify({"error": "请输入查询内容"}), 400

    # Step 1: 解析用户查询
    parsed = parse_query_with_context(user_query, DEFAULT_CITY)
    city = parsed.get("city", DEFAULT_CITY).strip() or DEFAULT_CITY
    keyword = parsed.get("keyword", "餐厅").strip() or "餐厅"
    location_desc = parsed.get("location_desc", "").strip()

    # Step 2: 地理编码（如有）
    center = ""
    if location_desc:
        center = geocode_address(location_desc, city)

    # Step 3: 搜索 POI
    pois = search_poi(city, keyword, center)

    # Step 4: 生成密度洞察
    insight = generate_density_insight(pois, radius_m=500)

    # Step 5: 格式化结果
    results = []
    for i, poi in enumerate(pois[:15], 1):
        name = poi.get("name", "")
        address = poi.get("address", "") or "暂无地址"
        phone = poi.get("phone", "") or "暂无"
        location = poi.get("location", "")
        lng, lat = location.split(",") if location else ("", "")
        pid = poi.get("id", "")
        ptype = poi.get("type", "未知")

        results.append({
            "id": i,
            "name": name,
            "address": address,
            "phone": phone,
            "lng": lng,
            "lat": lat,
            "detail_url": f"https://www.amap.com/place/{pid}" if pid else "#",
            "type": ptype
        })

    return jsonify({
        "query": user_query,
        "insight": insight,
        "total": len(results),
        "results": results
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')