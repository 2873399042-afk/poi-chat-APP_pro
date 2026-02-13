'''''from flask import Flask, request, render_template, jsonify
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

# 存储对话上下文
conversation_context = {}


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
3. 如果用户意图模糊（如"附近有啥""有什么地方"），优先猜测高频需求：
   - 白天 → 咖啡店、餐厅、便利店
   - 晚上 → 餐厅、KTV
   - 提到"买药""不舒服" → 药店、医院
   - 提到"停车""加油" → 停车场、加油站
4. location_desc 只填具体地点名（如"新街口""中关村"），不要填"附近"

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
    return "餐厅"  # 默认比"商店"更合理


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
    session_id = data.get("session_id", "default_session")  # 获取会话ID

    if not user_query:
        return jsonify({"error": "请输入查询内容"}), 400

    # 获取上一次的城市信息（如果存在）
    last_city = DEFAULT_CITY
    if session_id in conversation_context:
        last_city = conversation_context[session_id].get("last_city", DEFAULT_CITY)

    # Step 1: 解析用户查询
    parsed = parse_query_with_context(user_query, last_city)
    city = parsed.get("city", DEFAULT_CITY).strip() or DEFAULT_CITY
    keyword = parsed.get("keyword", "餐厅").strip() or "餐厅"
    location_desc = parsed.get("location_desc", "").strip()

    # 更新会话上下文
    conversation_context[session_id] = {
        "last_city": city,
        "last_keyword": keyword,
        "last_location_desc": location_desc
    }

    # Step 2: 地理编码（如有）
    center = ""
    if location_desc:
        center = geocode_address(location_desc, city)

    # Step 3: 搜索 POI
    pois = search_poi(city, keyword, center)

    # Step 4: 生成密度洞察
    insight = generate_density_insight(pois, radius_m=600)

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
    print(f"即将返回给前端的数据: 洞察: {insight}, 结果数: {len(results)}")

    return jsonify({
        "query": user_query,
        "insight": insight,
        "total": len(results),
        "results": results,
        "session_id": session_id  # 返回会话ID
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
'''
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

# 存储对话上下文
conversation_context = {}


def parse_query_with_context(user_query, last_city=DEFAULT_CITY):
    """使用 Qwen 解析用户NLP，强制 keyword 在标准 POI 白名单中"""

    # 鲁棒性改进：定义标准 POI 类型白名单
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
3. 如果用户意图模糊（如"附近有啥""有什么地方"），优先猜测高频需求：
   - 白天 → 咖啡店、餐厅、便利店
   - 晚上 → 餐厅、KTV
   - 提到"买药""不舒服" → 药店、医院
   - 提到"停车""加油" → 停车场、加油站
4. location_desc 只填具体地点名（如"新街口""中关村"），不要填"附近"

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
    return "餐厅"  # 默认比"商店"更合理


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
    """调用高德 POI 搜索接口，空结果返回fallback"""

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


# --- 动态空间分析函数 ---
def generate_density_insight(pois, radius_m=500):
    """
    动态空间分析引擎：根据 POI 类型动态切换分析策略
    """
    if not pois:
        return "📍 未找到相关数据，无法进行空间分析。"

    # 提取有效坐标点
    points = []
    for poi in pois:
        try:
            location = poi.get("location", "")
            if location and "," in location:
                lng, lat = map(float, location.split(","))
                points.append((lng, lat))
        except:
            continue

    if len(points) < 2:
        return "ℹ️ 数据点过少，无法生成深度空间洞察。"

    # 获取第一个 POI 的名称用于判断类型
    sample_poi_name = pois[0].get('name', '').lower() if pois else ''

    # --- 策略 1: 餐饮/零售类分析 (竞争与聚集) ---
    retail_keywords = ['咖啡', '奶茶', '餐厅', '饭店', '快餐', '小吃', '火锅', '烧烤', '甜品', '饮品', '便利店', '超市']
    if any(kw in sample_poi_name for kw in retail_keywords):
        return _analyze_competition_insight(points, pois, radius_m)

    # --- 策略 2: 公共服务/生活类分析 (稀缺与覆盖) ---
    public_keywords = ['医院', '诊所', '药店', '学校', '小学', '中学', '大学', '派出所', '消防', '社区', '公园',
                       '体育馆']
    if any(kw in sample_poi_name for kw in public_keywords):
        return _analyze_public_service_insight(points, pois, radius_m)

    # --- 策略 3: 交通/设施类分析 (可达性与均匀度) ---
    facility_keywords = ['加油站', '充电', '停车场', '充电站', '充电桩', '公交', '地铁']
    if any(kw in sample_poi_name for kw in facility_keywords):
        return _analyze_facility_distribution(points, pois, radius_m)

    # --- 默认策略: 通用密度分析 ---
    return _analyze_generic_density(points, pois, radius_m)


def _analyze_competition_insight(points, pois, radius_m):
    """餐饮/零售类：侧重于竞争激烈程度和商圈聚集度"""
    # 计算平均密度
    densities = [_count_neighbors(p, points, radius_m) for p in points]
    avg_density = sum(densities) / len(densities)

    # 计算聚集度（标准差，越小越均匀，越大越聚集）
    variance = sum((d - avg_density) ** 2 for d in densities) / len(densities)
    std_dev = variance ** 0.5

    keyword = "该类店铺"
    if pois:
        keyword = pois[0].get('type', '该类').split(';')[0]  # 简单取一级分类

    if std_dev > avg_density * 0.6:  # 标准差大，说明分布不均，有扎堆现象
        return (f"💰 <strong>商业洞察：{keyword}呈现明显的集聚效应</strong>。"
                f"数据表明该区域存在核心商圈，店铺倾向于扎堆经营，竞争较为激烈。")
    elif avg_density > 4:
        return (f"💰 <strong>商业洞察：{keyword}分布高度密集</strong>。"
                f"该区域商业成熟度高，客流潜力大，但新进入者将面临激烈竞争。")
    else:
        return (f"💰 <strong>商业洞察：{keyword}分布较为分散</strong>。"
                f"市场处于均衡状态，各店铺服务半径清晰，竞争相对缓和。")


def _analyze_public_service_insight(points, pois, radius_m):
    """公共服务类：侧重于覆盖率和稀缺性"""
    # 计算最大空隙（任意两点间的最大距离，粗略估计覆盖盲区）
    max_gap = 0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            dist = haversine(points[i][0], points[i][1], points[j][0], points[j][1])
            max_gap = max(max_gap, dist)

    keyword = "该类设施"
    if pois:
        keyword = pois[0].get('type', '该类').split(';')[0]

    if max_gap > 2000:  # 如果最大空隙超过2公里
        return (f"🏥 <strong>公共服务洞察：{keyword}呈现点状分布，覆盖范围广但有盲区</strong>。"
                f"区域内设施间隔较远，可能存在服务真空地带，建议关注偏远区域的需求。")
    elif len(points) >= 5:
        return (f"🏥 <strong>公共服务洞察：{keyword}呈现网络化高密度覆盖</strong>。"
                f"该区域公共服务配套非常完善，居民获取服务的便利性极高。")
    else:
        return (f"🏥 <strong>公共服务洞察：{keyword}呈现核心节点分布</strong>。"
                f"设施数量适中，主要集中在核心区域，能够满足大部分基础需求。")


def _analyze_facility_distribution(points, pois, radius_m):
    """设施类（加油站/停车场）：侧重于可达性和均匀度"""
    # 设施类希望分布均匀，所以标准差越小越好
    densities = [_count_neighbors(p, points, radius_m) for p in points]
    variance = sum((d - sum(densities) / len(densities)) ** 2 for d in densities) / len(densities)

    keyword = "该类设施"
    if pois:
        keyword = pois[0].get('type', '该类').split(';')[0]

    if variance < 1:
        return (f"⛽ <strong>设施布局洞察：{keyword}呈现均匀网格化布局</strong>。"
                f"分布策略旨在最大化覆盖范围，确保区域内任何位置都能便捷到达。")
    else:
        return (f"⛽ <strong>设施布局洞察：{keyword}呈现需求导向型布局</strong>。"
                f"设施倾向于集中在车流量大的主干道或特定区域，部分区域可能存在排队压力。")


def _analyze_generic_density(points, pois, radius_m):
    """通用密度分析（回退方案）"""
    # 计算平均密度
    densities = [_count_neighbors(p, points, radius_m) for p in points]
    avg_density = sum(densities) / len(densities)

    keyword = "目标设施"
    if pois:
        keyword = pois[0].get('type', '目标设施').split(';')[0]

    if avg_density >= 5:
        desc = "高度密集，商业氛围浓厚"
    elif avg_density >= 3:
        desc = "分布较密集，选择丰富"
    elif avg_density >= 2:
        desc = "分布较为稀疏，环境安静"
    else:
        desc = "极为稀疏，具有独占性"

    return f"🌐 空间洞察：该区域{keyword}分布{desc}（平均 {avg_density:.1f} 家/{radius_m}m 半径）。"


def _count_neighbors(point, points, radius_m):
    """辅助函数：计算某点周围半径内的邻居数量"""
    count = 0
    for other in points:
        if point != other:
            dist = haversine(point[0], point[1], other[0], other[1])
            if dist <= radius_m:
                count += 1
    return count





@app.route('/')
def index():
    return render_template('index.html')


@app.route('/search', methods=['POST'])
def search():
    data = request.get_json()
    user_query = data.get("query", "").strip()
    session_id = data.get("session_id", "default_session")  # 获取会话ID

    if not user_query:
        return jsonify({"error": "请输入查询内容"}), 400

    # 获取上一次的城市信息（如果存在）
    last_city = DEFAULT_CITY
    if session_id in conversation_context:
        last_city = conversation_context[session_id].get("last_city", DEFAULT_CITY)

    # Step 1: 解析用户查询
    parsed = parse_query_with_context(user_query, last_city)
    city = parsed.get("city", DEFAULT_CITY).strip() or DEFAULT_CITY
    keyword = parsed.get("keyword", "餐厅").strip() or "餐厅"
    location_desc = parsed.get("location_desc", "").strip()

    # 更新会话上下文
    conversation_context[session_id] = {
        "last_city": city,
        "last_keyword": keyword,
        "last_location_desc": location_desc
    }

    # Step 2: 地理编码
    center = ""
    if location_desc:
        center = geocode_address(location_desc, city)

    # Step 3: 搜索 POI
    pois = search_poi(city, keyword, center)

    # Step 4: 生成动态空间洞察
    insight = generate_density_insight(pois, radius_m=600)

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
    print(f"即将返回给前端的数据: 洞察: {insight}, 结果数: {len(results)}")

    return jsonify({
        "query": user_query,
        "insight": insight,
        "total": len(results),
        "results": results,
        "session_id": session_id  # 返回会话ID
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')